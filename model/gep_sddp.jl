# gep_sddp.jl -- annual multistage stochastic GEP model (SDDP.jl + Gurobi).
# Usage: julia --project=. model/gep_sddp.jl ETH [scenario]

using SDDP, JuMP, Gurobi, JSON, Statistics, Random, LinearAlgebra, Printf

const MONEY = 1.0e6           # cost coefficients divided by this -> objective in $ million
const GRB_ENV = Gurobi.Env()  # one shared environment

# 5-node Gauss-Hermite discretization of N(0,1): (node, prob)
const GH_X = [-2.856970013872805, -1.355626179974266, 0.0,
               1.355626179974266,  2.856970013872805]
const GH_P = [0.011257411327720693, 0.2220759220056126, 0.5333333333333333,
              0.2220759220056126, 0.011257411327720693]

# Gauss-Hermite nodes/weights for E[f(Z)], Z ~ N(0,1), via Golub-Welsch; n=5 reproduces (GH_X, GH_P).
function gauss_hermite_normal(n::Int)
    n == 5 && return (GH_X, GH_P)
    J = SymTridiagonal(zeros(n), [sqrt(k) for k in 1:(n - 1)])
    F = eigen(J)
    return (F.values, (F.vectors[1, :]) .^ 2)   # weights sum to 1
end
# The discretization the current scenario trains on; set in prep_scenario.
const GH_CURRENT = Ref{Tuple{Vector{Float64},Vector{Float64}}}((GH_X, GH_P))

# ----------------------------------------------------------------------------
# Stopping rule: compare a one-sided 95% upper bound (m_ub fixed-seed in-sample paths)
# with the SDDP lower bound; stop when the relative gap holds within rel_tol for
# stable_needed consecutive checks.
mutable struct RobustGapUpper <: SDDP.AbstractStoppingRule
    rel_tol::Float64
    min_iter::Int
    stable_needed::Int
    m_ub::Int
    z::Float64
    seed::Int
    check_every::Int
    stable::Int
end
RobustGapUpper(; rel_tol = 0.03, min_iter = 30, stable_needed = 5,
               m_ub = 500, z = 1.6448536269514722, seed = 13579, check_every = 5) =
    RobustGapUpper(rel_tol, min_iter, stable_needed, m_ub, z, seed, check_every, 0)

SDDP.stopping_rule_status(::RobustGapUpper) = :robust_gap_upper

function SDDP.convergence_test(model::SDDP.PolicyGraph, log::Vector{SDDP.Log},
                               rule::RobustGapUpper)
    length(log) < rule.min_iter && return false
    # Run the expensive UB simulation only every `check_every` iterations after min_iter.
    (length(log) - rule.min_iter) % rule.check_every != 0 && return false
    LB = log[end].bound
    rng_saved = copy(Random.default_rng())      # snapshot the training RNG before the reproducible UB draw
    Random.seed!(rule.seed)                     # fixed in-sample UB path set (drawn identically each check)
    sims = SDDP.simulate(model, rule.m_ub)
    copy!(Random.default_rng(), rng_saved)      # restore it: the UB draw must not perturb forward-pass sampling
    costs = Float64[sum(d[:stage_objective] for d in path) for path in sims]
    ubm = Statistics.mean(costs)
    ubsd = length(costs) >= 2 ? Statistics.std(costs) : 0.0   # sample standard deviation (n-1 denominator)
    ubu = ubm + rule.z * ubsd / sqrt(max(1, rule.m_ub))       # one-sided 95% upper CI
    gu  = ubu - LB
    rgu = gu / max(1e-9, abs(LB))
    ok  = (gu >= -0.005 * abs(LB)) && (abs(rgu) <= rule.rel_tol)
    rule.stable = ok ? rule.stable + 1 : 0
    return rule.stable >= rule.stable_needed
end

# ----------------------------------------------------------------------------
# Effective scenario data (annual formulation)
# ----------------------------------------------------------------------------
struct Eff
    tech::Vector{String}
    nE::Int
    NT::Int
    disc::Float64
    voll::Float64                       # $/MWh value of lost load (c3)
    a1::Vector{Float64}                 # $/MWh variable (c1)
    a2::Matrix{Float64}                 # NT x nE $/MW investment (c2, incl. mult/learning)
    b1::Vector{Float64}                 # $/MW-yr fixed O&M (c4)
    EM::Vector{Float64}                 # kg/MWh emissions intensity
    xinit::Vector{Float64}              # MW initial installed capacity
    ub::Vector{Float64}                 # MW total-capacity ceiling (Xbar)
    lead::Vector{Int}                   # construction lead time (years)
    Lmax::Int
    qinit::Matrix{Float64}              # nE x Lmax initial construction pipeline (MW); bucket k = years to commissioning
    accum::Matrix{Float64}              # NT x nE cumulative mandatory-retirement floor F
    central::Vector{Float64}            # NT central annual demand phibar (MWh)
    rho::Float64
    sigma_frac::Float64                 # sigma0 (relative innovation)
    hydro_idx::Int
    eta::Vector{Float64}                # MWh/MW-yr annual energy-availability coefficient = 8760 f_e
    mu::Vector{Float64}                 # normalized hydrology multipliers per condition (L,N,H)
    hP::Matrix{Float64}                 # 3x3 hydrology transition matrix P^H
    hInit::Vector{Float64}              # initial hydrology node distribution (h1)
    tau::Float64                        # $/kg carbon tax (0 if none)
    cap::Union{Nothing,Vector{Float64}} # annual emission cap (kg) per stage, or nothing
    salv_frac::Float64                  # terminal salvage fraction of last-period CAPEX
    refurb_coal::Float64                # $/MW-yr coal life-extension cost (coal-exit scenario)
    fix_retire::Bool                    # force cumulative retirement == floor
    needs_slack::Bool                   # penalized capacity recourse slack (coal-exit only)
end

getd(scn, k, default) = haskey(scn, k) && scn[k] !== nothing ? scn[k] : default

function prep_scenario(P, scn)
    tech = String.(P["tech"]); nE = Int(P["nE"]); NT = length(P["years"])
    a1 = Float64.(P["a1"]); b1 = Float64.(P["b1"]); EM = Float64.(P["EM"])
    xinit = Float64.(P["xinit"])
    a2 = reduce(vcat, [reshape(Float64.(r), 1, nE) for r in P["a2"]])   # NT x nE
    accum = reduce(vcat, [reshape(Float64.(r), 1, nE) for r in P["accum"]])
    central = Float64.(P["central"])
    eta = Float64.(P["eta"])                                            # 8760 * f_e
    ubv = Float64.(P["ub"]); lead = Int.(P["lead"]); Lmax = maximum(lead)
    tidx = Dict(t => i for (i, t) in enumerate(tech))
    hydro_idx = tidx["Hydro"]

    # initial construction pipeline (nE x Lmax); bucket k = remaining years to commissioning
    qinit = zeros(nE, Lmax)
    if haskey(P, "qinit") && P["qinit"] !== nothing
        for (t, kv) in P["qinit"]                                       # {tech => {k => MW}}
            e = tidx[t]
            for (k, v) in kv; qinit[e, parse(Int, string(k))] = Float64(v); end
        end
    end

    # --- investment cost multipliers / learning ---
    if haskey(scn, "inv_mult")
        for (t, f) in scn["inv_mult"]; a2[:, tidx[t]] .*= Float64(f); end
    end
    if haskey(scn, "inv_learning")
        for (t, se) in scn["inv_learning"]
            s, e = Float64(se[1]), Float64(se[2])
            for k in 1:NT
                a2[k, tidx[t]] *= s + (e - s) * (k - 1) / (NT - 1)
            end
        end
    end

    # --- demand path overrides (accelerated-access / high-demand cases) ---
    central_eff = copy(central)
    if haskey(scn, "demand_path") && scn["demand_path"] !== nothing
        central_eff = Float64.(scn["demand_path"])
    end
    if haskey(scn, "demand_cagr") && scn["demand_cagr"] !== nothing
        g = Float64(scn["demand_cagr"]); central_eff = [central[1] * (1 + g)^(k - 1) for k in 1:NT]
    end
    if haskey(scn, "demand_mult") && scn["demand_mult"] !== nothing
        central_eff .*= Float64(scn["demand_mult"])
    end

    # --- ceilings ---
    ub = copy(ubv)
    if haskey(scn, "ub_override")
        for (t, v) in scn["ub_override"]; ub[tidx[t]] = Float64(v); end
    end
    if haskey(scn, "new_build_cap")
        for (t, v) in scn["new_build_cap"]; ub[tidx[t]] = xinit[tidx[t]] + Float64(v); end
    end

    # --- hydrology (values embedded in params from hydro_calibration.json) ---
    mu = Float64.(P["hydro"]["mu"])
    hP = reduce(vcat, [reshape(Float64.(r), 1, 3) for r in P["hydro"]["P"]])
    hInit = Float64.(P["hydro"]["init"])
    if getd(scn, "hydrology", "") == "dry_persistent"
        hInit = [1.0, 0.0, 0.0]; hP = [1.0 0.0 0.0; 1.0 0.0 0.0; 1.0 0.0 0.0]
    end
    if haskey(scn, "hydro_persist") && scn["hydro_persist"] !== nothing
        a = Float64(scn["hydro_persist"]); pistat = Float64.(P["hydro"]["stationary"])
        hP = a >= 0 ? (1 - a) .* hP .+ a .* Matrix{Float64}(I, 3, 3) :
                      (1 + a) .* hP .+ (-a) .* (ones(3) * pistat')
    end

    # --- carbon policy instruments: tax and annual cap ---
    tau = Float64(getd(scn, "carbon_tax", 0.0))
    cap = nothing
    if haskey(scn, "emission_cap") && scn["emission_cap"] !== nothing
        ec = scn["emission_cap"]; cap = ec isa Number ? fill(Float64(ec), NT) : Float64.(ec)
    end

    # --- coal retirement decomposition (coal-exit scenario) ---
    fix_retire = false; refurb_coal = 0.0
    mode = getd(scn, "coal_retire", "")
    if mode == "fixed"
        fix_retire = true
    elseif mode == "delayed"
        cidx = get(tidx, "Coal", 0)
        if cidx > 0
            shifted = zeros(NT, nE); shift = 5
            for k in 1:NT; src = max(1, k - shift); shifted[k, :] = accum[src, :]; end
            shifted[:, cidx] = [k <= shift ? 0.0 : accum[k - shift, cidx] for k in 1:NT]
            accum = shifted
        end
    elseif mode == "refurb"
        refurb_coal = Float64(P["coal_refurb_usd_per_kw"]) * 1000.0
    end

    salv = P["salvage"] == true ? Float64(getd(P, "salvage_frac", 0.30)) : 0.0

    # --- scalar overrides for sensitivity / uncertainty-decomposition runs ---
    disc_r  = Float64(getd(scn, "discount", P["discount"]))
    voll_v  = Float64(getd(scn, "voll", P["voll"]))
    rho_v   = Float64(P["demand"]["rho"])
    sigma_v = Float64(P["demand"]["sigma_frac"])
    GH_CURRENT[] = gauss_hermite_normal(Int(getd(scn, "gh_nodes", 5)))
    if haskey(scn, "mu_dry_mult") && scn["mu_dry_mult"] !== nothing
        mu = copy(mu); mu[1] *= Float64(scn["mu_dry_mult"])
    end
    getd(scn, "det_demand", false) == true && (sigma_v = 0.0)
    if getd(scn, "det_hydro", false) == true          # deterministic expected hydrology (E[mu]=1)
        mu = [1.0, 1.0, 1.0]; hP = [0.0 1.0 0.0; 0.0 1.0 0.0; 0.0 1.0 0.0]; hInit = [0.0, 1.0, 0.0]
    end

    Eff(tech, nE, NT, disc_r, voll_v, a1, a2, b1, EM, xinit, ub, lead, Lmax, qinit,
        accum, central_eff, rho_v, sigma_v, hydro_idx, eta, mu, hP, hInit,
        tau, cap, salv, refurb_coal, fix_retire, mode != "")
end

# ----------------------------------------------------------------------------
# SDDP model builder (annual formulation)
# ----------------------------------------------------------------------------
function build_sddp(E::Eff)
    TM = Vector{Matrix{Float64}}(undef, E.NT)
    TM[1] = reshape(E.hInit, 1, 3)
    for t in 2:E.NT; TM[t] = E.hP; end

    disc(t) = (1 + E.disc)^(-(t - 1))
    lb0 = E.salv_frac > 0 ?
        -disc(E.NT) * E.salv_frac * sum(E.a2[E.NT, e] * E.ub[e] for e in 1:E.nE) / MONEY : 0.0

    model = SDDP.MarkovianPolicyGraph(
        transition_matrices = TM,
        sense = :Min,
        lower_bound = lb0,
        optimizer = () -> Gurobi.Optimizer(GRB_ENV),
    ) do sp, node
        t, m = node                       # stage (year), hydrology condition index (1=L,2=N,3=H)
        nE, Lmax = E.nE, E.Lmax
        coal_idx = findfirst(==("Coal"), E.tech)
        hi = E.hydro_idx

        # ---- continuous states: x = (X, Q, S, u) ----
        @variable(sp, X[e = 1:nE] >= 0, SDDP.State, initial_value = E.xinit[e])
        @variable(sp, Q[e = 1:nE, k = 1:Lmax] >= 0, SDDP.State, initial_value = E.qinit[e, k])
        @variable(sp, S[e = 1:nE] >= 0, SDDP.State, initial_value = 0.0)
        @variable(sp, u, SDDP.State, initial_value = 0.0)

        # ---- annual controls ----
        @variable(sp, I[e = 1:nE] >= 0)                 # investment committed this year (MW)
        @variable(sp, R[e = 1:nE] >= 0)                 # retirement of inherited fleet (MW)
        @variable(sp, G[e = 1:nE] >= 0)                 # annual generation (MWh)
        @variable(sp, Z >= 0)                           # annual unserved energy (MWh)
        if E.needs_slack
            @variable(sp, capslack[e = 1:nE] >= 0)
        else
            capslack = zeros(nE)
        end

        Dbar = E.central[t]
        # ---- annual energy balance ----
        @constraint(sp, sum(G[e] for e in 1:nE) + Z == Dbar + u.in)
        # ---- non-hydro annual availability: G_e <= eta_e X_e ----
        for e in 1:nE
            e != hi && @constraint(sp, G[e] <= E.eta[e] * X[e].in)
        end
        # ---- stochastic reservoir hydro: G_hy <= eta_hy mu_h X_hy ----
        @constraint(sp, G[hi] <= E.eta[hi] * E.mu[m] * X[hi].in)

        # ---- capacity / pipeline / retirement dynamics ----
        for e in 1:nE
            L = E.lead[e]
            @constraint(sp, X[e].out == X[e].in + Q[e, 1].in + (L == 1 ? I[e] : 0.0) - R[e])
            for k in 1:Lmax-1
                @constraint(sp, Q[e, k].out == Q[e, k+1].in + (k == L - 1 ? I[e] : 0.0))
            end
            @constraint(sp, Q[e, Lmax].out == (Lmax == L - 1 ? I[e] : 0.0))
            @constraint(sp, R[e] <= E.xinit[e] - S[e].in)          # retire inherited fleet only
            @constraint(sp, S[e].out == S[e].in + R[e])
            @constraint(sp, S[e].in + R[e] >= E.accum[t, e])       # mandatory retirement floor F
            if E.fix_retire
                @constraint(sp, S[e].out <= E.accum[t, e] + 1e-6)
            end
            @constraint(sp, X[e].out <= E.ub[e] + capslack[e])
            @constraint(sp, I[e] <= E.ub[e] - X[e].in - sum(Q[e, k].in for k in 1:Lmax) + R[e] + capslack[e])
        end

        # ---- demand state transition (innovation set in parameterize) ----
        @constraint(sp, demand_con, u.out - E.rho * u.in == 0)

        # ---- annual emissions cap (no cumulative budget) ----
        @expression(sp, emis, sum((E.EM[e] / 1e9) * G[e] for e in 1:nE))   # Mt
        if E.cap !== nothing
            @constraint(sp, emis <= E.cap[t] / 1e9)
        end

        # ---- stage objective: planning cost Ctilde = C_t + tau*sum EM_e G_e ----
        # (the tax term enters dispatch; the tax-free social cost C_t is recovered in write_result)
        var_op   = sum((E.a1[e] + E.tau * E.EM[e]) * G[e] for e in 1:nE)
        invest   = sum(E.a2[t, e] * I[e] for e in 1:nE)
        fixed_om = sum(E.b1[e] * X[e].in for e in 1:nE)
        refurb   = (E.refurb_coal > 0 && coal_idx !== nothing) ?
                   E.refurb_coal * (E.xinit[coal_idx] - S[coal_idx].in) : 0.0
        unserved = E.voll * Z
        cap_pen  = 1e8 * sum(capslack[e] for e in 1:nE)
        stage = disc(t) * (var_op + invest + fixed_om + unserved + refurb + cap_pen) / MONEY
        if t == E.NT && E.salv_frac > 0
            stage -= disc(t) * E.salv_frac *
                     sum(E.a2[E.NT, e] * (X[e].out - E.xinit[e] + S[e].out) for e in 1:E.nE) / MONEY
        end
        @stageobjective(sp, stage)

        # ---- parameterize demand innovation (AR(1), stagewise-independent) ----
        sigma_t = E.sigma_frac * Dbar
        if sigma_t > 0
            ghx, ghp = GH_CURRENT[]; Ω = [sigma_t * x for x in ghx]; Pω = ghp
        else
            Ω = [0.0]; Pω = [1.0]
        end
        SDDP.parameterize(sp, Ω, Pω) do ω
            JuMP.set_normalized_rhs(demand_con, ω)
        end
    end
    return model
end

# ----------------------------------------------------------------------------
# Train, simulate out of sample, summarize
# ----------------------------------------------------------------------------
function simulate_summary(model, E; N = 1000, seeds = [1, 2, 3, 4, 5])
    vars = [:X, :G, :I, :R, :S, :Z, :u]
    disc(t) = (1 + E.disc)^(-(t - 1))
    coal_idx = findfirst(==("Coal"), E.tech)
    keys_ = (:cost, :emis, :unmet, :res, :tax, :voll, :salv, :refurb)
    per = Dict(k => Float64[] for k in keys_)
    sims_ref = nothing
    for (si, sd) in enumerate(seeds)
        Random.seed!(sd)
        sims = SDDP.simulate(model, N, vars)
        si == 1 && (sims_ref = sims)
        for path in sims
            c = sum(stage[:stage_objective] for stage in path) * MONEY   # planner objective (incl. tax)
            em = 0.0; un = 0.0; res = 0.0; tax = 0.0; voll = 0.0; refurb = 0.0
            for (t, stage) in enumerate(path)
                G = stage[:G]; I = stage[:I]; X = stage[:X]
                em  += sum(E.EM[e] * G[e] for e in 1:E.nE)
                un  += stage[:Z]
                # gross real resource: investment + fixed O&M (start-of-year stock) + variable (no tax)
                res += disc(t) * (sum(E.a1[e] * G[e] for e in 1:E.nE) +
                                  sum(E.a2[t, e] * I[e] for e in 1:E.nE) +
                                  sum(E.b1[e] * X[e].in for e in 1:E.nE))
                tax  += disc(t) * E.tau * sum(E.EM[e] * G[e] for e in 1:E.nE)   # tax payment (transfer)
                voll += disc(t) * E.voll * stage[:Z]                            # reliability cost
                if E.refurb_coal > 0 && coal_idx !== nothing
                    refurb += disc(t) * E.refurb_coal * (E.xinit[coal_idx] - stage[:S][coal_idx].in)
                end
            end
            salv = 0.0
            if E.salv_frac > 0
                XN = path[E.NT][:X]; SN = path[E.NT][:S]
                salv = disc(E.NT) * E.salv_frac *
                       sum(E.a2[E.NT, e] * (XN[e].out - E.xinit[e] + SN[e].out) for e in 1:E.nE)
            end
            push!(per[:cost], c); push!(per[:emis], em / 1e9); push!(per[:unmet], un / 1e6)
            push!(per[:res], res); push!(per[:tax], tax); push!(per[:voll], voll)
            push!(per[:salv], salv); push!(per[:refurb], refurb)
        end
    end
    ci(v) = 1.96 * std(v) / sqrt(length(v))
    mn(v) = mean(v)
    return Dict(
        "oos_cost_BUSD_mean" => mn(per[:cost]) / 1e9,      # planner objective (incl. tax)
        "oos_cost_BUSD_ci"   => ci(per[:cost]) / 1e9,
        "oos_emis_Mt_mean"   => mn(per[:emis]),
        "oos_unmet_TWh_mean" => mn(per[:unmet]),
        # OOS cost decomposition (all discounted $B, same sample as the objective):
        # objective = res + tax + voll - salv + refurb  (up to the capacity-slack penalty)
        "oos_res_BUSD"    => mn(per[:res]) / 1e9,
        "oos_tax_BUSD"    => mn(per[:tax]) / 1e9,
        "oos_voll_BUSD"   => mn(per[:voll]) / 1e9,
        "oos_salv_BUSD"   => mn(per[:salv]) / 1e9,
        "oos_refurb_BUSD" => mn(per[:refurb]) / 1e9,
    ), sims_ref
end

# Mean annual trajectories from simulations
function mean_trajectories(sims, E)
    NT = E.NT; nE = E.nE
    cap = zeros(nE, NT); gen = zeros(nE, NT); build = zeros(nE, NT)
    retire = zeros(nE, NT); unmet = zeros(NT); emis = zeros(NT)
    n = length(sims)
    for path in sims
        for (t, stage) in enumerate(path)
            for e in 1:nE
                cap[e, t] += stage[:X][e].out
                build[e, t] += stage[:I][e]
                retire[e, t] += stage[:R][e]
                gen[e, t] += stage[:G][e]
            end
            unmet[t] += stage[:Z]
            emis[t] += sum(E.EM[e] * stage[:G][e] for e in 1:nE)
        end
    end
    cap ./= n; gen ./= n; build ./= n; retire ./= n; unmet ./= n; emis ./= n
    return cap, gen, build, retire, unmet, emis
end

# ----------------------------------------------------------------------------
# JSON output for the plotting layer
# ----------------------------------------------------------------------------
function write_result(path, E, scn, model, oos, sims; extra = Dict())
    cap, gen, build, retire, unmet, emis = mean_trajectories(sims, E)
    lb = SDDP.calculate_bound(model)
    disc = [(1 + E.disc)^(-(t - 1)) for t in 1:E.NT]
    # concessional-finance transfer (combined_tax50_solar): computed on mean build path
    fin = get(scn, "finance_subsidy", nothing); finance_subsidy = 0.0
    if fin !== nothing
        fe = findfirst(==(fin["tech"]), E.tech); mlt = Float64(fin["mult"])
        if fe !== nothing && mlt > 0
            finance_subsidy = sum(disc[t] * (E.a2[t, fe] / mlt) * (1 - mlt) * build[fe, t] for t in 1:E.NT) / 1e9
        end
    end

    # --- cost accounting: OOS-consistent decomposition (reconciles with the objective) ---
    # planner objective = gross_resource + tax_payment + voll_cost - salvage + refurb (+ capacity-slack penalty)
    gross_resource = oos["oos_res_BUSD"]      # investment + fixed O&M + variable (NO tax, NO VoLL)
    tax_payment    = oos["oos_tax_BUSD"]      # carbon-tax payment (transfer to government)
    voll_cost      = oos["oos_voll_BUSD"]     # reliability cost = VoLL * unserved
    salvage_cost   = oos["oos_salv_BUSD"]     # terminal salvage credit on new-build
    refurb_cost    = oos["oos_refurb_BUSD"]   # coal life-extension (coal-exit refurb only)
    planner_obj    = oos["oos_cost_BUSD_mean"]
    # net resource cost: gross resource cost less the terminal salvage credit.
    net_resource   = gross_resource - salvage_cost
    # total expected cost: net resource cost plus discounted shortage cost and, where the
    # scenario applies one, coal life-extension cost. Excludes the carbon-tax transfer.
    total_expected = gross_resource + voll_cost - salvage_cost + refurb_cost
    recon_resid    = planner_obj - (gross_resource + tax_payment + voll_cost - salvage_cost + refurb_cost)
    eue_TWh        = oos["oos_unmet_TWh_mean"]

    res = Dict(
        "meta" => merge(Dict("scenario" => scn["name"], "label" => scn["label"],
                             "lower_bound_BUSD" => lb * MONEY / 1e9,
                             "carbon_tax" => E.tau, "cap_active" => E.cap !== nothing), extra),
        "years" => collect(2025:2025 + E.NT - 1),
        "tech" => E.tech,
        "capacity_MW" => [cap[e, :] for e in 1:E.nE],
        "build_MW" => [build[e, :] for e in 1:E.nE],
        "retire_MW" => [retire[e, :] for e in 1:E.nE],
        "gen_TWh" => [gen[e, :] ./ 1e6 for e in 1:E.nE],
        "unserved_TWh" => unmet ./ 1e6,
        "emissions_Mt" => emis ./ 1e9,                # mean annual path (also the matched-cap trajectory)
        "avg_emissions_Mt" => mean(emis ./ 1e9),
        "y2050_emissions_Mt" => emis[end] / 1e9,
        # --- cost fields (all OOS, all $B discounted) ---
        "gross_resource_BUSD" => gross_resource,       # inv + FOM + variable (no tax, no VoLL)
        "net_resource_cost_BUSD" => net_resource,      # gross - salvage
        "total_expected_cost_BUSD" => total_expected,  # gross + VoLL - salvage + refurb, excl. tax
        "voll_cost_BUSD" => voll_cost,
        "tax_payment_BUSD" => tax_payment,
        "salvage_BUSD" => salvage_cost,
        "refurb_cost_BUSD" => refurb_cost,
        "planner_objective_BUSD" => planner_obj,       # = objective, includes tax payment
        "reconciliation_residual_BUSD" => recon_resid, # should be ~0 (nonzero => capacity-slack penalty)
        "finance_subsidy_BUSD" => finance_subsidy,
        "eue_TWh" => eue_TWh,
        # duplicate fields retained for convenience
        "resource_cost_BUSD" => gross_resource,
        "cash_cost_BUSD" => gross_resource + tax_payment,
        "tax_transfer_BUSD" => tax_payment,
        "reliability_cost_BUSD" => voll_cost,
        "total_cost_BUSD" => planner_obj,
        "oos" => oos,
    )
    open(path, "w") do io; JSON.print(io, res, 2); end
    return res
end

# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------
function run_scenario(P, scn; outdir, extra = Dict())
    E = prep_scenario(P, scn)
    model = build_sddp(E)
    @info "Training $(scn["name"])"
    Random.seed!(12345)                              # reproducible forward-pass sampling
    reltol = Float64(getd(scn, "rel_tol", 0.03))
    itcap = Int(getd(scn, "iteration_limit", 200))
    wall_s = @elapsed SDDP.train(model; iteration_limit = itcap, risk_measure = SDDP.Expectation(),
               stopping_rules = [RobustGapUpper(rel_tol = reltol, min_iter = 30,
                                                stable_needed = 5, m_ub = 500)],
               print_level = 1)
    n_iter = try; length(model.most_recent_training_results.log); catch; -1; end
    @info "trained $(scn["name"])  iters=$n_iter  wall=$(round(wall_s, digits = 1))s"
    flush(stdout); flush(stderr)
    extra = merge(extra, Dict("wall_s" => wall_s, "n_iter" => n_iter))
    oos, sims = simulate_summary(model, E)
    mkpath(outdir)
    out = joinpath(outdir, "$(scn["name"]).json")
    res = write_result(out, E, scn, model, oos, sims; extra = extra)
    @info "wrote $out  cost=$(round(oos["oos_cost_BUSD_mean"],digits=3)) B  emis=$(round(oos["oos_emis_Mt_mean"],digits=2)) Mt"
    flush(stdout); flush(stderr)
    return E, oos, Float64.(res["emissions_Mt"])
end

# One calibration trial: solve with annual cap set to `value`, return oos expected
# cumulative emissions (Mt). Annual instrument only (no cumulative budget).
function trial_emissions(P, scn, value)
    s = Dict{String,Any}(scn); s["emission_cap"] = value
    E = prep_scenario(P, s); model = build_sddp(E)
    Random.seed!(12345)
    SDDP.train(model; iteration_limit = 200, print_level = 0,
               stopping_rules = [RobustGapUpper(rel_tol = 0.03, min_iter = 30, stable_needed = 5, m_ub = 500)])
    oos, _ = simulate_summary(model, E; seeds = [201, 202, 203, 204, 205])
    return oos["oos_emis_Mt_mean"]
end

# Matched stringency: bisect an ANNUAL cap so out-of-sample expected cumulative
# emissions match the reference tax scenario. (No cumulative-budget instrument.)
function calibrate_matched!(P, scn, target_Mt; tol = 0.02, maxit = 7)
    NT = length(P["years"])
    to_value(level) = fill(level * 1e9, NT)
    lo, hi = 0.3 * target_Mt / NT, 3.0 * target_Mt / NT
    flo = trial_emissions(P, scn, to_value(lo)) - target_Mt
    fhi = trial_emissions(P, scn, to_value(hi)) - target_Mt
    level = hi
    if fhi >= 0
        for it in 1:maxit
            level = 0.5 * (lo + hi)
            f = trial_emissions(P, scn, to_value(level)) - target_Mt
            @info "  calibrate $(scn["name"]) it=$it level=$(round(level, digits=4)) emis=$(round(f + target_Mt, digits=3))"
            abs(f) / target_Mt < tol && break
            sign(f) == sign(flo) ? (lo = level; flo = f) : (hi = level; fhi = f)
        end
    else
        @warn "matched $(scn["name"]): target $target_Mt Mt not reachable; using loosest level"
    end
    scn["emission_cap"] = to_value(level)
    scn["matched_level_Mt"] = level; scn["matched_target_Mt"] = target_Mt
    return scn
end

function main()
    cc = length(ARGS) >= 1 ? ARGS[1] : "ETH"
    P = JSON.parsefile(joinpath(@__DIR__, "..", "data", "params_$(cc).json"))
    outdir = joinpath(@__DIR__, "..", "results", cc)
    only = length(ARGS) >= 2 ? ARGS[2] : nothing
    expemis = Dict{String,Float64}()
    for scn in P["scenarios"]
        if only === nothing || scn["name"] == only
            extra = Dict{String,Any}()
            ref = get(scn, "match_emissions_of", nothing)
            if ref !== nothing
                haskey(expemis, ref) || error("matched scenario $(scn["name"]) needs $ref run first")
                calibrate_matched!(P, scn, expemis[ref])
                extra = Dict("matched_level_Mt" => scn["matched_level_Mt"],
                             "matched_target_Mt" => scn["matched_target_Mt"], "matched_ref" => ref)
            end
            _, oos, _ = run_scenario(P, scn; outdir = outdir, extra = extra)
            expemis[scn["name"]] = oos["oos_emis_Mt_mean"]
        end
    end
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end
