###############################################################################
# gep_sddp.jl  --  Multistage stochastic generation-expansion model (SDDP.jl + Gurobi)
#
# Builds and solves the multistage stochastic generation-expansion planning
# model for one country and writes one JSON result file per scenario. The model
# combines:
#   - a demand-deviation AR(1) state  u_t
#   - a three-state hydrology Markov graph
#   - four seasonal operating blocks
#   - lead-time construction-pipeline states Q_{e,k}
#   - a resource-cost vs carbon-tax transfer split (computed ex post)
#   - annual-cap and cumulative-budget emission instruments
#   - a terminal salvage credit
#
# Reads params_<CC>.json (from export_params.py). Writes results per scenario.
# The deterministic (expected-value) and perfect-information benchmarks live in
# benchmarks.jl and reuse prep_scenario and the deterministic builder here.
#
# Usage:
#   julia --project=. gep_sddp.jl ETH            # all Ethiopia scenarios
#   julia --project=. gep_sddp.jl ZWE baseline   # one scenario
###############################################################################

using SDDP, JuMP, Gurobi, JSON, Statistics, Random, LinearAlgebra, Printf

const MONEY = 1.0e6           # cost coefficients divided by this -> objective in $ million
const GRB_ENV = Gurobi.Env()  # one shared environment

# 5-node Gauss-Hermite discretization of N(0,1): (node, prob)
const GH_X = [-2.856970013872805, -1.355626179974266, 0.0,
               1.355626179974266,  2.856970013872805]
const GH_P = [0.011257411327720693, 0.2220759220056126, 0.5333333333333333,
              0.2220759220056126, 0.011257411327720693]

# Gauss-Hermite nodes/weights for E[f(Z)], Z ~ N(0,1), by Golub-Welsch on the
# probabilists' Hermite Jacobi matrix (off-diagonal sqrt(k), zero diagonal).
# n = 5 reproduces (GH_X, GH_P); higher n supports a discretization-refinement check.
function gauss_hermite_normal(n::Int)
    n == 5 && return (GH_X, GH_P)
    J = SymTridiagonal(zeros(n), [sqrt(k) for k in 1:(n - 1)])
    F = eigen(J)
    return (F.values, (F.vectors[1, :]) .^ 2)   # weights sum to 1
end
# The discretization the current scenario trains on; set in prep_scenario.
const GH_CURRENT = Ref{Tuple{Vector{Float64},Vector{Float64}}}((GH_X, GH_P))

# ----------------------------------------------------------------------------
# Stopping rule: an upper-bound / lower-bound optimality-gap criterion.
# Each iteration after `min_iter`, the policy is re-simulated on a *fixed* set of
# `m_ub` in-sample paths (the same paths every iteration, fixed by `seed`), the
# one-sided 95% upper confidence bound UBu = mean + z*sd/sqrt(m_ub) is formed
# (z = 1.645, the one-sided 95% quantile), and training stops once the relative
# gap (UBu - LB)/|LB| stays within `rel_tol` for `stable_needed` consecutive
# iterations. Reusing a fixed path set makes the gap a smooth function of the
# policy, so the stability count is not reset by Monte-Carlo jitter, which lets
# convergence be detected reliably where a fresh-sample UB would not.
mutable struct RobustGapUpper <: SDDP.AbstractStoppingRule
    rel_tol::Float64
    min_iter::Int
    stable_needed::Int
    m_ub::Int
    z::Float64
    seed::Int
    stable::Int
end
RobustGapUpper(; rel_tol = 0.03, min_iter = 30, stable_needed = 5,
               m_ub = 500, z = 1.6448536269514722, seed = 13579) =
    RobustGapUpper(rel_tol, min_iter, stable_needed, m_ub, z, seed, 0)

SDDP.stopping_rule_status(::RobustGapUpper) = :robust_gap_upper

function SDDP.convergence_test(model::SDDP.PolicyGraph, log::Vector{SDDP.Log},
                               rule::RobustGapUpper)
    length(log) < rule.min_iter && return false
    LB = log[end].bound
    Random.seed!(rule.seed)                     # fixed in-sample UB path set (drawn identically each check)
    sims = SDDP.simulate(model, rule.m_ub)
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
# Scenario preparation: turn base params + overrides into effective arrays
# ----------------------------------------------------------------------------
struct Eff
    tech::Vector{String}
    nE::Int
    NT::Int
    disc::Float64
    voll::Float64
    a1::Vector{Float64}                 # $/MWh
    a2::Matrix{Float64}                 # NT x nE  $/MW (effective, incl. mult/learning)
    b1::Vector{Float64}                 # $/MW-yr fixed O&M
    EM::Vector{Float64}                 # kg/MWh
    xinit::Vector{Float64}
    ub::Vector{Float64}                 # effective total-capacity ceiling
    lead::Vector{Int}
    Lmax::Int
    accum::Matrix{Float64}              # NT x nE cumulative min-retirement (effective)
    central::Vector{Float64}            # NT effective central demand
    rho::Float64
    sigma_frac::Float64
    # blocks
    nB::Int
    H::Vector{Float64}                  # block hours
    dshare::Vector{Float64}             # block demand share
    cf::Matrix{Float64}                 # nE x nB block capacity factors (fraction)
    season::Vector{Int}                 # block season: 1=wet, 2=dry
    hydro_idx::Int                      # index of Hydro in tech
    hydro_avail::Float64                # reservoir turbine power availability
    water_wet::Float64                  # share of annual water energy in wet season
    water_dry::Float64                  # share in dry season
    mu::Vector{Float64}                 # hydrology multipliers per regime
    hP::Matrix{Float64}                 # hydrology transition
    hInit::Vector{Float64}
    # policy
    tau::Float64                        # $/kg carbon tax
    cap::Union{Nothing,Vector{Float64}} # annual emission cap (kg) per stage
    budget::Union{Nothing,Float64}      # cumulative emission budget (kg)
    salv_frac::Float64                  # terminal salvage fraction of last CAPEX
    refurb_coal::Float64                # $/MW-yr life-extension cost (0 unless refurb)
    fix_retire::Bool                    # force cumulative retirement == floor
    alpha::Vector{Float64}              # annual energy factor (MWh/MW-yr), hydro water budget
    reserve_margin::Float64             # planning reserve margin
    cap_short_price::Float64            # $/MW-yr penalty for unmet reserve
    cap_credit::Vector{Float64}         # firm capacity credit at the peak, per tech
    peak_factor::Float64                # peak load (MW) per MWh of annual demand
    # optional battery storage; storage_capex == 0 disables it entirely
    storage_capex::Float64              # $/MW of power capacity (bundled duration)
    storage_eff::Float64                # round-trip efficiency
    storage_credit::Float64             # firm capacity credit of storage at the peak
    storage_fom::Float64                # $/MW-yr fixed O&M
    needs_slack::Bool                   # enable penalized capacity recourse slack (coal-exit scenarios only)
end

getd(scn, k, default) = haskey(scn, k) && scn[k] !== nothing ? scn[k] : default

function prep_scenario(P, scn)
    tech = String.(P["tech"]); nE = Int(P["nE"]); NT = length(P["years"])
    a1 = Float64.(P["a1"]); b1 = Float64.(P["b1"]); EM = Float64.(P["EM"])
    xinit = Float64.(P["xinit"])
    a2 = reduce(vcat, [reshape(Float64.(r), 1, nE) for r in P["a2"]])   # NT x nE
    accum = reduce(vcat, [reshape(Float64.(r), 1, nE) for r in P["accum"]])
    central = Float64.(P["central"])
    alpha = Float64.(P["alpha"])
    reserve_margin = Float64(P["reserve_margin"]); cap_short_price = Float64(P["cap_short_price"])
    cap_credit = Float64.(P["cap_credit"]); peak_factor = Float64(P["peak_factor"])
    ubv = Float64.(P["ub"]); lead = Int.(P["lead"]); Lmax = maximum(lead)

    tidx = Dict(t => i for (i, t) in enumerate(tech))

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

    # --- demand path ---
    central_eff = copy(central)
    if haskey(scn, "demand_path") && scn["demand_path"] !== nothing
        central_eff = Float64.(scn["demand_path"])
    end
    if haskey(scn, "demand_cagr") && scn["demand_cagr"] !== nothing
        g = Float64(scn["demand_cagr"])
        central_eff = [central[1] * (1 + g)^(k - 1) for k in 1:NT]
    end
    if haskey(scn, "demand_mult") && scn["demand_mult"] !== nothing
        central_eff .*= Float64(scn["demand_mult"])
    end

    # --- ceilings (ub_override and new_build_cap both act on the total ceiling) ---
    ub = copy(ubv)
    if haskey(scn, "ub_override")
        for (t, v) in scn["ub_override"]; ub[tidx[t]] = Float64(v); end
    end
    if haskey(scn, "new_build_cap")
        for (t, v) in scn["new_build_cap"]; ub[tidx[t]] = xinit[tidx[t]] + Float64(v); end
    end

    # --- blocks ---
    H = Float64.(P["blocks"]["hours"]); dshare = Float64.(P["blocks"]["demand_share"])
    nB = length(H)
    cf = zeros(nE, nB)
    for (i, t) in enumerate(tech); cf[i, :] = Float64.(P["blocks"]["cf"][t]); end
    season = Int.(P["blocks"]["season"])
    hydro_idx = tidx["Hydro"]
    hydro_avail = Float64(P["hydro_avail"])
    water_wet = Float64(P["water_wet"]); water_dry = Float64(P["water_dry"])

    # 12-block temporal-resolution refinement: subdivide each seasonal block into
    # three equal-hour, equal-demand sub-blocks that resolve intra-block solar
    # variation (evening dip vs midday surplus) through a mean-preserving
    # solar-capacity-factor shape. Demand stays flat within each block, exactly as
    # the four-block model assumes, so the subdivision introduces no new peak
    # beyond the annual peak_factor and tests convergence in the number of blocks.
    if getd(scn, "blocks_12", false) == true
        solar_i = tidx["Solar"]
        sf = [0.85, 1.0, 1.15]                       # solar-cf shape, mean 1 -> block energy preserved
        H2 = Float64[]; d2 = Float64[]; s2 = Int[]; cols = Vector{Vector{Float64}}()
        for b in 1:nB, j in 1:3
            push!(H2, H[b] / 3); push!(d2, dshare[b] / 3); push!(s2, season[b])
            col = copy(cf[:, b]); col[solar_i] = cf[solar_i, b] * sf[j]; push!(cols, col)
        end
        H = H2; dshare = d2; season = s2; nB = length(H2); cf = reduce(hcat, cols)
    end

    # --- hydrology ---
    mu = Float64.(P["hydro"]["mu"])
    hP = reduce(vcat, [reshape(Float64.(r), 1, 3) for r in P["hydro"]["P"]])
    hInit = Float64.(P["hydro"]["init"])
    if getd(scn, "hydrology", "") == "dry_persistent"
        hInit = [1.0, 0.0, 0.0]
        hP = [1.0 0.0 0.0; 1.0 0.0 0.0; 1.0 0.0 0.0]
    end
    # Hydrology-persistence sensitivity: blend the transition matrix toward the
    # identity (a>0, more persistent) or toward the stationary rows (a<0, less
    # persistent). Both preserve the stationary hydrology distribution, so only
    # drought clustering changes.
    if haskey(scn, "hydro_persist") && scn["hydro_persist"] !== nothing
        a = Float64(scn["hydro_persist"]); pistat = Float64.(P["hydro"]["stationary"])
        hP = a >= 0 ? (1 - a) .* hP .+ a .* Matrix{Float64}(I, 3, 3) :
                      (1 + a) .* hP .+ (-a) .* (ones(3) * pistat')
    end

    # --- carbon policy ---
    tau = Float64(getd(scn, "carbon_tax", 0.0))
    cap = nothing
    if haskey(scn, "emission_cap") && scn["emission_cap"] !== nothing
        ec = scn["emission_cap"]
        cap = ec isa Number ? fill(Float64(ec), NT) : Float64.(ec)
    end
    budget = haskey(scn, "cumulative_budget") && scn["cumulative_budget"] !== nothing ?
             Float64(scn["cumulative_budget"]) : nothing

    # --- coal retirement decomposition ---
    fix_retire = false; refurb_coal = 0.0
    mode = getd(scn, "coal_retire", "")
    if mode == "fixed"
        fix_retire = true
    elseif mode == "delayed"
        cidx = get(tidx, "Coal", 0)
        if cidx > 0
            shifted = zeros(NT, nE); shift = 5
            for k in 1:NT
                src = max(1, k - shift); shifted[k, :] = accum[src, :]
            end
            shifted[:, cidx] = [k <= shift ? 0.0 : accum[k - shift, cidx] for k in 1:NT]
            accum = shifted
        end
    elseif mode == "refurb"
        refurb_coal = Float64(P["coal_refurb_usd_per_kw"]) * 1000.0  # $/kW -> $/MW-yr proxy
    end

    salv = P["salvage"] == true ? Float64(getd(P, "salvage_frac", 0.30)) : 0.0

    # --- scalar overrides for sensitivity and uncertainty-decomposition runs ---
    disc_r  = Float64(getd(scn, "discount", P["discount"]))            # discount-rate sensitivity
    voll_v  = Float64(getd(scn, "voll", P["voll"]))                    # VoLL sensitivity
    rho_v   = Float64(P["demand"]["rho"])
    sigma_v = Float64(P["demand"]["sigma_frac"])
    GH_CURRENT[] = gauss_hermite_normal(Int(getd(scn, "gh_nodes", 5)))  # discretization-refinement check
    if haskey(scn, "reserve_margin") && scn["reserve_margin"] !== nothing
        reserve_margin = Float64(scn["reserve_margin"])               # reserve-margin sensitivity
    end
    if haskey(scn, "cap_credit_mult") && scn["cap_credit_mult"] !== nothing
        cap_credit = cap_credit .* Float64(scn["cap_credit_mult"])     # capacity-credit sensitivity
    end
    if haskey(scn, "mu_dry_mult") && scn["mu_dry_mult"] !== nothing
        mu = copy(mu); mu[1] *= Float64(scn["mu_dry_mult"])           # dry-multiplier sensitivity
    end
    # uncertainty decomposition: switch off individual sources of uncertainty
    getd(scn, "det_demand", false) == true && (sigma_v = 0.0)         # deterministic demand
    if getd(scn, "det_hydro", false) == true                          # deterministic normal hydrology
        mu = [1.0, 1.0, 1.0]; hP = [0.0 1.0 0.0; 0.0 1.0 0.0; 0.0 1.0 0.0]; hInit = [0.0, 1.0, 0.0]
    end
    # single annual operating block (temporal-resolution comparison)
    if getd(scn, "annual_blocks", false) == true
        orig_peak = peak_factor               # four-block seasonal peak, preserved if keep_peak
        cf1 = [sum(cf[e, b] * H[b] for b in 1:nB) / sum(H) for e in 1:nE]
        cf = reshape(cf1, nE, 1); H = [sum(H)]; dshare = [1.0]; season = [1]; nB = 1
        water_wet = 1.0; water_dry = 0.0
        # default: annual model sees average load; keep_peak: retain the seasonal peak
        # requirement to isolate temporal energy aggregation from lower peak adequacy.
        peak_factor = getd(scn, "keep_peak", false) == true ? orig_peak : dshare[1] / H[1]
    end

    storage_capex  = Float64(getd(scn, "storage_capex", 0.0))
    storage_eff    = Float64(getd(scn, "storage_eff", 0.85))
    storage_credit = Float64(getd(scn, "storage_credit", 0.90))
    storage_fom    = Float64(getd(scn, "storage_fom", 0.0))

    Eff(tech, nE, NT, disc_r, voll_v,
        a1, a2, b1, EM, xinit, ub, lead, Lmax, accum, central_eff,
        rho_v, sigma_v,
        nB, H, dshare, cf, season, hydro_idx, hydro_avail, water_wet, water_dry, mu, hP, hInit,
        tau, cap, budget, salv, refurb_coal, fix_retire, alpha,
        reserve_margin, cap_short_price, cap_credit, peak_factor,
        storage_capex, storage_eff, storage_credit, storage_fom,
        mode != "")
end

# ----------------------------------------------------------------------------
# SDDP model builder
# ----------------------------------------------------------------------------
function build_sddp(E::Eff)
    # Markov transition matrices: stage 1 is 1x3 (root -> regimes), then 3x3.
    TM = Vector{Matrix{Float64}}(undef, E.NT)
    TM[1] = reshape(E.hInit, 1, 3)
    for t in 2:E.NT; TM[t] = E.hP; end

    disc(t) = (1 + E.disc)^(-(t - 1))

    model = SDDP.MarkovianPolicyGraph(
        transition_matrices = TM,
        sense = :Min,
        lower_bound = 0.0,
        optimizer = () -> Gurobi.Optimizer(GRB_ENV),
    ) do sp, node
        t, m = node                       # stage, hydrology regime index
        nE, nB, Lmax = E.nE, E.nB, E.Lmax
        coal_idx = findfirst(==("Coal"), E.tech)

        # ---- states ----
        @variable(sp, X[e = 1:nE] >= 0, SDDP.State, initial_value = E.xinit[e])
        @variable(sp, Q[e = 1:nE, k = 1:Lmax] >= 0, SDDP.State, initial_value = 0.0)
        @variable(sp, S[e = 1:nE] >= 0, SDDP.State, initial_value = 0.0)
        @variable(sp, u, SDDP.State, initial_value = 0.0)
        if E.budget !== nothing
            @variable(sp, B >= 0, SDDP.State, initial_value = E.budget / 1e9)   # Mt
        end

        # ---- controls ----
        @variable(sp, I[e = 1:nE] >= 0)                 # investment (enters pipeline bucket lead[e])
        @variable(sp, R[e = 1:nE] >= 0)                 # retirement (existing only)
        # Relatively-complete-recourse slack on the capacity ceiling: lets a subproblem
        # stay feasible in the rare states where forced retirement plus wide-demand
        # investment would overshoot the ceiling. Penalized prohibitively below, so it is
        # zero in any optimal policy. Enabled only for the coal-exit scenarios that need it
        # (E.needs_slack); elsewhere capslack is a constant 0, leaving the model bit-identical.
        if E.needs_slack
            @variable(sp, capslack[e = 1:nE] >= 0)
        else
            capslack = zeros(nE)
        end
        @variable(sp, G[e = 1:nE, b = 1:nB] >= 0)       # block generation (MWh)
        @variable(sp, Z[b = 1:nB] >= 0)                 # block unserved (MWh)
        @variable(sp, rshort >= 0)                      # planning reserve shortfall (MW)

        # ---- optional battery storage: intra-year shifting + firm capacity ----
        has_stor = E.storage_capex > 0
        if has_stor
            @variable(sp, Xs >= 0, SDDP.State, initial_value = 0.0)   # power capacity (MW)
            @variable(sp, Is >= 0)                                    # storage investment (MW)
            @variable(sp, ch[b = 1:nB] >= 0)                          # charge (MWh) in block
            @variable(sp, dis[b = 1:nB] >= 0)                         # discharge (MWh) in block
            @constraint(sp, [b = 1:nB], ch[b] <= Xs.in * E.H[b])
            @constraint(sp, [b = 1:nB], dis[b] <= Xs.in * E.H[b])
            @constraint(sp, sum(dis[b] for b in 1:nB) <= E.storage_eff * sum(ch[b] for b in 1:nB))
            @constraint(sp, Xs.out == Xs.in + Is)
        end
        stor_net(b) = has_stor ? (dis[b] - ch[b]) : 0.0

        Dbar = E.central[t]
        # ---- block energy balance ----
        @constraint(sp, [b = 1:nB],
            sum(G[e, b] for e in 1:nE) + stor_net(b) + Z[b] == E.dshare[b] * (Dbar + u.in))
        # non-hydro block availability (VRE resource shape or dispatchable power limit)
        for e in 1:nE, b in 1:nB
            if e != E.hydro_idx
                @constraint(sp, G[e, b] <= E.cf[e, b] * E.H[b] * X[e].in)
            end
        end
        # reservoir hydro: turbine power limit per block + a single annual water-energy
        # budget scaled by the hydrology regime. The reservoir allocates its annual
        # energy across blocks (stores wet inflow, releases in the dry season).
        hi = E.hydro_idx
        @constraint(sp, [b = 1:nB], G[hi, b] <= E.hydro_avail * E.H[b] * X[hi].in)
        # seasonal water-energy budgets: wet and dry seasons each get a share of the
        # regime-scaled annual water energy, so dry-season hydro is genuinely scarce
        # rather than freely shiftable across the whole year.
        @constraint(sp, sum(G[hi, b] for b in 1:nB if E.season[b] == 1) <=
                        E.mu[m] * E.water_wet * E.alpha[hi] * X[hi].in)
        @constraint(sp, sum(G[hi, b] for b in 1:nB if E.season[b] == 2) <=
                        E.mu[m] * E.water_dry * E.alpha[hi] * X[hi].in)

        # ---- planning reserve margin (firm capacity vs dry-year peak) ----
        @constraint(sp, sum(E.cap_credit[e] * X[e].in for e in 1:nE) +
                        (has_stor ? E.storage_credit * Xs.in : 0.0) + rshort >=
                        (1 + E.reserve_margin) * E.peak_factor * (Dbar + u.in))

        # ---- capacity / pipeline / retirement dynamics ----
        for e in 1:nE
            L = E.lead[e]
            # Construction pipeline: a commitment made in year t commissions
            # after exactly L years. It enters bucket L-1 of next year's pipeline and
            # advances one bucket per year; a one-year project commissions directly into
            # next year's capacity. Bucket 1 always commissions in the following year.
            @constraint(sp, X[e].out == X[e].in + Q[e, 1].in + (L == 1 ? I[e] : 0.0) - R[e])
            for k in 1:Lmax-1
                @constraint(sp, Q[e, k].out == Q[e, k+1].in + (k == L - 1 ? I[e] : 0.0))
            end
            @constraint(sp, Q[e, Lmax].out == (Lmax == L - 1 ? I[e] : 0.0))
            # retire only existing, remaining un-retired stock
            @constraint(sp, R[e] <= E.xinit[e] - S[e].in)
            @constraint(sp, S[e].out == S[e].in + R[e])
            # mandatory retirement floor (schedule)
            @constraint(sp, S[e].in + R[e] >= E.accum[t, e])
            if E.fix_retire
                @constraint(sp, S[e].out <= E.accum[t, e] + 1e-6)   # no economic retirement beyond floor
            end
            @constraint(sp, X[e].out <= E.ub[e] + capslack[e])
            # investment headroom: installed + everything already in the pipeline + new
            # commitment, net of retirement this stage, may not exceed the ceiling.
            # Guarantees X.out <= ub stays feasible at every future stage (recourse).
            @constraint(sp, I[e] <= E.ub[e] - X[e].in - sum(Q[e, k].in for k in 1:Lmax) + R[e] + capslack[e])
        end

        # ---- demand state transition (noise set in parameterize) ----
        @constraint(sp, demand_con, u.out - E.rho * u.in == 0)

        # ---- emissions accounting (in Mt to keep the constraint well scaled) ----
        @expression(sp, emis, sum((E.EM[e] / 1e9) * G[e, b] for e in 1:nE, b in 1:nB))  # Mt
        if E.cap !== nothing
            @constraint(sp, emis <= E.cap[t] / 1e9)
        end
        if E.budget !== nothing
            @constraint(sp, emis <= B.in)            # B carried in Mt
            @constraint(sp, B.out == B.in - emis)
        end

        # ---- stage objective (minimized, $ million; tax included so dispatch responds) ----
        gen(e) = sum(G[e, b] for b in 1:nB)
        var_op   = sum((E.a1[e] + E.tau * E.EM[e]) * gen(e) for e in 1:nE)
        invest   = sum(E.a2[t, e] * I[e] for e in 1:nE)
        fixed_om = sum(E.b1[e] * X[e].in for e in 1:nE)
        refurb   = (E.refurb_coal > 0 && coal_idx !== nothing) ?
                   E.refurb_coal * (E.xinit[coal_idx] - S[coal_idx].in) : 0.0
        unserved = E.voll * sum(Z[b] for b in 1:nB)
        reserve_pen = E.cap_short_price * rshort
        stor_cost = has_stor ? (E.storage_capex * Is + E.storage_fom * Xs.in) : 0.0
        cap_pen = 1e8 * sum(capslack[e] for e in 1:nE)   # prohibitive: keeps capslack at 0 unless needed for feasibility
        stage = disc(t) * (var_op + invest + fixed_om + unserved + refurb + reserve_pen + stor_cost + cap_pen) / MONEY

        # terminal salvage credit for NEW-BUILD capacity still standing (removes end-effect
        # bias). Only capacity built within the horizon, X - (xinit - S), receives a credit;
        # the inherited pre-2025 fleet is sunk and gets none.
        if t == E.NT && E.salv_frac > 0
            stage -= disc(t) * E.salv_frac * sum(E.a2[E.NT, e] * (X[e].out - E.xinit[e] + S[e].out) for e in 1:nE) / MONEY
        end
        @stageobjective(sp, stage)

        # ---- parameterize demand innovation ----
        # A zero-variance innovation (sigma_t == 0, the deterministic-demand
        # decomposition cases) must collapse to a SINGLE support point. Passing five
        # identical Gauss-Hermite nodes at 0.0 corrupts SDDP cut generation and yields a
        # meaningless lower bound, so branch on the degenerate case explicitly.
        sigma_t = E.sigma_frac * Dbar
        if sigma_t > 0
            ghx, ghp = GH_CURRENT[]
            Ω = [sigma_t * x for x in ghx]; Pω = ghp
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
# Train, simulate out of sample, and summarize
# ----------------------------------------------------------------------------
function simulate_summary(model, E; N = 1000, seeds = [1, 2, 3, 4, 5])
    vars = [:X, :G, :I, :R, :Z, :u, :rshort]
    per_seed = Dict{Symbol,Vector{Float64}}(:cost => Float64[], :emis => Float64[],
                                            :unmet => Float64[], :rshort => Float64[],
                                            :rshort_MWyr => Float64[], :rshort_bind => Float64[],
                                            :rshort_max => Float64[])
    # accumulate mean trajectories from the first seed for reporting
    sims_ref = nothing
    for (si, sd) in enumerate(seeds)
        Random.seed!(sd)
        sims = SDDP.simulate(model, N, vars)
        if si == 1; sims_ref = sims; end
        for path in sims
            c = sum(stage[:stage_objective] for stage in path) * MONEY
            em = 0.0; un = 0.0
            for stage in path
                G = stage[:G]
                em += sum(E.EM[e] * sum(G[e, b] for b in 1:E.nB) for e in 1:E.nE)
                un += sum(stage[:Z][b] for b in 1:E.nB)
            end
            rsv = [stage[:rshort] for stage in path]     # per-year reserve shortfall (MW)
            rs = sum(rsv)                                 # summed over stages (MW-year over the horizon)
            push!(per_seed[:cost], c)
            push!(per_seed[:emis], em / 1e9)     # Mt cumulative
            push!(per_seed[:unmet], un / 1e6)    # TWh cumulative
            push!(per_seed[:rshort], rs / E.NT)  # mean reserve shortfall (MW)
            # physical reserve-adequacy metrics from the simulated paths
            push!(per_seed[:rshort_MWyr], rs)                         # expected reserve shortfall (MW-year)
            push!(per_seed[:rshort_bind], count(>(1e-3), rsv) / E.NT) # share of years with positive shortfall
            push!(per_seed[:rshort_max], maximum(rsv))                # worst-year shortfall on the path (MW)
        end
    end
    ci(v) = 1.96 * std(v) / sqrt(length(v))
    return Dict(
        "oos_cost_BUSD_mean" => mean(per_seed[:cost]) / 1e9,
        "oos_cost_BUSD_ci"   => ci(per_seed[:cost]) / 1e9,
        "oos_emis_Mt_mean"   => mean(per_seed[:emis]),
        "oos_unmet_TWh_mean" => mean(per_seed[:unmet]),
        "oos_reserve_short_MW_mean" => mean(per_seed[:rshort]),
        "oos_reserve_short_MWyr_mean" => mean(per_seed[:rshort_MWyr]),   # expected reserve shortfall (MW-year)
        "oos_reserve_bind_frac_mean"  => mean(per_seed[:rshort_bind]),   # share of years binding
        "oos_reserve_short_max_MW"    => maximum(per_seed[:rshort_max]), # maximum annual shortfall (MW)
    ), sims_ref
end

# Mean trajectories (capacity, generation-by-block, emissions) from simulations
function mean_trajectories(sims, E)
    NT = E.NT; nE = E.nE; nB = E.nB
    cap = zeros(nE, NT); genb = zeros(nE, NT, nB); build = zeros(nE, NT)
    retire = zeros(nE, NT); unmet = zeros(NT, nB); emis = zeros(NT)
    n = length(sims)
    for path in sims
        for (t, stage) in enumerate(path)
            for e in 1:nE
                cap[e, t] += stage[:X][e].out       # state var: outgoing value
                build[e, t] += stage[:I][e]
                retire[e, t] += stage[:R][e]
                for b in 1:nB; genb[e, t, b] += stage[:G][e, b]; end
            end
            for b in 1:nB; unmet[t, b] += stage[:Z][b]; end
            emis[t] += sum(E.EM[e] * sum(stage[:G][e, b] for b in 1:nB) for e in 1:nE)
        end
    end
    cap ./= n; genb ./= n; build ./= n; retire ./= n; unmet ./= n; emis ./= n
    return cap, genb, build, retire, unmet, emis
end

# ----------------------------------------------------------------------------
# JSON output for the plotting layer
# ----------------------------------------------------------------------------
function write_result(path, E, scn, model, oos, sims; extra = Dict())
    cap, genb, build, retire, unmet, emis = mean_trajectories(sims, E)
    genyr = dropdims(sum(genb, dims = 3), dims = 3)      # nE x NT (MWh)
    lb = SDDP.calculate_bound(model)
    # cost decomposition on the mean trajectory (NPV, $B)
    disc = [(1 + E.disc)^(-(t - 1)) for t in 1:E.NT]
    econ = sum(disc[t] * (sum(E.a1[e] * genyr[e, t] for e in 1:E.nE) +
                          sum(E.a2[t, e] * build[e, t] for e in 1:E.nE) +
                          sum(E.b1[e] * cap[e, t] for e in 1:E.nE)) for t in 1:E.NT) / 1e9
    tax_transfer = E.tau * sum(disc[t] * E.EM[e] * genyr[e, t] for e in 1:E.nE, t in 1:E.NT) / 1e9
    # concessional-finance subsidy: the public cost of financing the technology
    # at a concessional rather than commercial WACC. a2 already carries the effective
    # (concessionally financed) CAPEX = full * mult, so the subsidy per MW is full*(1-mult).
    fin = get(scn, "finance_subsidy", nothing)
    finance_subsidy = 0.0
    if fin !== nothing
        fe = findfirst(==(fin["tech"]), E.tech); mlt = Float64(fin["mult"])
        if fe !== nothing && mlt > 0
            finance_subsidy = sum(disc[t] * (E.a2[t, fe] / mlt) * (1 - mlt) * build[fe, t]
                                  for t in 1:E.NT) / 1e9
        end
    end
    eue_TWh = sum(unmet) / 1e6
    reliability_cost = sum(disc[t] * E.voll * sum(unmet[t, b] for b in 1:E.nB) for t in 1:E.NT) / 1e9
    salvage_cost = E.salv_frac > 0 ? disc[E.NT] * E.salv_frac *
        sum(E.a2[E.NT, e] * (cap[e, E.NT] - E.xinit[e] + sum(retire[e, :])) for e in 1:E.nE) / 1e9 : 0.0
    res = Dict(
        "meta" => merge(Dict("scenario" => scn["name"], "label" => scn["label"],
                             "lower_bound_BUSD" => lb * MONEY / 1e9,
                             "carbon_tax" => E.tau,
                             "cap_active" => E.cap !== nothing,
                             "budget_active" => E.budget !== nothing),
                        extra),
        "years" => collect(2025:2050),
        "tech" => E.tech,
        "blocks" => ["wet_peak", "wet_off", "dry_peak", "dry_off"],
        "capacity_MW" => [cap[e, :] for e in 1:E.nE],
        "build_MW" => [build[e, :] for e in 1:E.nE],
        "retire_MW" => [retire[e, :] for e in 1:E.nE],
        "gen_TWh" => [genyr[e, :] ./ 1e6 for e in 1:E.nE],
        "gen_TWh_block" => [[genb[e, :, b] ./ 1e6 for b in 1:E.nB] for e in 1:E.nE],
        "unserved_TWh" => [unmet[:, b] ./ 1e6 for b in 1:E.nB],
        "emissions_Mt" => emis ./ 1e9,
        "avg_emissions_Mt" => mean(emis ./ 1e9),
        "y2050_emissions_Mt" => emis[end] / 1e9,
        "resource_cost_BUSD" => econ,                       # economic: inv + O&M + fuel
        "cash_cost_BUSD" => econ + tax_transfer,            # utility cash incl. tax transfer
        "tax_transfer_BUSD" => tax_transfer,
        "finance_subsidy_BUSD" => finance_subsidy,          # concessional-finance public cost (NPV)
        "reliability_cost_BUSD" => reliability_cost,        # VoLL * unserved (NPV)
        "salvage_BUSD" => salvage_cost,                     # terminal salvage credit (NPV)
        "eue_TWh" => eue_TWh,                               # expected unserved energy
        "reserve_short_MW" => get(oos, "oos_reserve_short_MW_mean", 0.0),
        "reserve_short_MWyr" => get(oos, "oos_reserve_short_MWyr_mean", 0.0),   # expected reserve shortfall (MW-year)
        "reserve_bind_frac" => get(oos, "oos_reserve_bind_frac_mean", 0.0),     # share of years with positive shortfall
        "reserve_short_max_MW" => get(oos, "oos_reserve_short_max_MW", 0.0),    # maximum annual shortfall (MW)
        "total_cost_BUSD" => oos["oos_cost_BUSD_mean"],     # simulated objective (all terms)
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
    # Risk measure: default risk-neutral; a scenario carrying "risk_lambda" trains
    # under a nested mean-CVaR, lambda*E + (1-lambda)*AVaR_beta.
    rm = getd(scn, "risk_lambda", nothing) === nothing ? SDDP.Expectation() :
         SDDP.EAVaR(lambda = Float64(scn["risk_lambda"]),
                    beta   = Float64(getd(scn, "risk_beta", 0.1)))
    # UB-LB optimality-gap stopping rule, applied identically to both countries,
    # with a generous iteration cap as a backstop. A scenario may loosen rel_tol if
    # a run cannot reach the default gap.
    reltol = Float64(getd(scn, "rel_tol", 0.03))
    # A scenario may raise the iteration backstop so a case that has not yet met the
    # statistical gap (e.g. combined tax + lower-solar-cost) can train further.
    itcap = Int(getd(scn, "iteration_limit", 200))
    wall_s = @elapsed SDDP.train(model; iteration_limit = itcap, risk_measure = rm,
               stopping_rules = [RobustGapUpper(rel_tol = reltol, min_iter = 30,
                                                stable_needed = 5, m_ub = 500)],
               print_level = 1)
    n_iter = try; length(model.most_recent_training_results.log); catch; -1; end
    @info "trained $(scn["name"])  iters=$n_iter  wall=$(round(wall_s, digits = 1))s"
    flush(stdout); flush(stderr)                     # force buffered log output when stdout is redirected to a file
    extra = merge(extra, Dict("wall_s" => wall_s, "n_iter" => n_iter))
    oos, sims = simulate_summary(model, E)
    mkpath(outdir)
    out = joinpath(outdir, "$(scn["name"]).json")
    res = write_result(out, E, scn, model, oos, sims; extra = extra)
    @info "wrote $out  cost=$(round(oos["oos_cost_BUSD_mean"],digits=3)) B  emis=$(round(oos["oos_emis_Mt_mean"],digits=2)) Mt"
    flush(stdout); flush(stderr)
    return E, oos, Float64.(res["emissions_Mt"])   # Mt per year (mean trajectory)
end

# One calibration trial: solve the scenario with instrument `field` set to `value`,
# and return the out-of-sample expected cumulative emissions (Mt).
function trial_emissions(P, scn, field, value)
    s = Dict{String,Any}(scn); s[field] = value
    E = prep_scenario(P, s)
    model = build_sddp(E)
    Random.seed!(12345)
    SDDP.train(model; iteration_limit = 200, print_level = 0,
               stopping_rules = [RobustGapUpper(rel_tol = 0.03, min_iter = 30,
                                                stable_needed = 5, m_ub = 500)])
    # Calibrate on a dedicated seed set, independent of the seeds used for the final
    # matched-policy evaluation in run_scenario, so tuning does not fit the evaluation sample.
    oos, _ = simulate_summary(model, E; seeds = [201, 202, 203, 204, 205])
    return oos["oos_emis_Mt_mean"]
end

# Matched stringency: calibrate an annual cap (flat, Mt/yr) or a cumulative budget
# (Mt) by bisection so the out-of-sample expected cumulative emissions equal the
# reference tax scenario's, within tolerance. Writes the calibrated level into scn.
function calibrate_matched!(P, scn, target_Mt; tol = 0.02, maxit = 7)
    inst = get(scn, "instrument", "annual_cap")
    # Tighten the cumulative-budget match to the cap's tolerance, so an asymmetric
    # tolerance cannot flatter the budget's cost advantage.
    if inst == "cumulative_budget"; tol = 0.005; maxit = 14; end
    NT = length(P["years"])
    field = inst == "cumulative_budget" ? "cumulative_budget" : "emission_cap"
    to_value(level) = inst == "cumulative_budget" ? level * 1e9 : fill(level * 1e9, NT)
    lo, hi = inst == "cumulative_budget" ? (target_Mt, 2.5 * target_Mt) :
                                           (0.3 * target_Mt / NT, 3.0 * target_Mt / NT)
    flo = trial_emissions(P, scn, field, to_value(lo)) - target_Mt
    fhi = trial_emissions(P, scn, field, to_value(hi)) - target_Mt
    level = hi
    if fhi < 0                                 # target exceeds achievable emissions: leave loose
        @warn "matched $(scn["name"]): target $target_Mt Mt not reachable; using loosest level"
    else
        for it in 1:maxit
            level = 0.5 * (lo + hi)
            f = trial_emissions(P, scn, field, to_value(level)) - target_Mt
            @info "  calibrate $(scn["name"]) it=$it level=$(round(level, digits=4)) emis=$(round(f + target_Mt, digits=3)) target=$(round(target_Mt, digits=3))"
            abs(f) / target_Mt < tol && break
            sign(f) == sign(flo) ? (lo = level; flo = f) : (hi = level; fhi = f)
        end
    end
    scn[field] = to_value(level)
    scn["matched_level_Mt"] = level
    scn["matched_target_Mt"] = target_Mt
    return scn
end

function main()
    cc = length(ARGS) >= 1 ? ARGS[1] : "ETH"
    P = JSON.parsefile(joinpath(@__DIR__, "params_$(cc).json"))
    outdir = joinpath(@__DIR__, "results", cc)
    only = length(ARGS) >= 2 ? ARGS[2] : nothing
    expemis = Dict{String,Float64}()             # expected cumulative emissions (Mt) by scenario
    for scn in P["scenarios"]
        if only === nothing || scn["name"] == only
            extra = Dict{String,Any}()
            ref = get(scn, "match_emissions_of", nothing)
            if ref !== nothing
                haskey(expemis, ref) || error("matched scenario $(scn["name"]) needs $ref run first")
                calibrate_matched!(P, scn, expemis[ref])
                extra = Dict("matched_level_Mt" => scn["matched_level_Mt"],
                             "matched_target_Mt" => scn["matched_target_Mt"],
                             "matched_ref" => ref)
            end
            _, oos, _ = run_scenario(P, scn; outdir = outdir, extra = extra)
            expemis[scn["name"]] = oos["oos_emis_Mt_mean"]
        end
    end
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end
