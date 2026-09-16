# benchmarks.jl -- value of adaptive planning (VSS) and of perfect information
# (EVPI) for the annual model, on common out-of-sample paths.
# Usage: julia --project=. model/benchmarks.jl <CC> [outdir]

include("gep_sddp.jl")   # brings prep_scenario, build_sddp, simulate_summary, Eff, GRB_ENV, MONEY into scope

# ----------------------------------------------------------------------------
# Deterministic multistage annual LP (JuMP + Gurobi) over a given realized path.
# `demand[t]` is the realized annual demand (MWh); `muvec[t]` the realized (or
# expected) hydrology multiplier at stage t. With `fixI` the investment schedule
# is frozen to a prescribed plan (used to operate the EV plan out of sample = EEV).
# ----------------------------------------------------------------------------
function build_det(E::Eff, demand::Vector{Float64}, muvec::Vector{Float64};
                   fixI = nothing)
    nE, Lmax, NT = E.nE, E.Lmax, E.NT
    coal_idx = findfirst(==("Coal"), E.tech)
    hi = E.hydro_idx
    m = Model(() -> Gurobi.Optimizer(GRB_ENV)); set_silent(m)
    disc(t) = (1 + E.disc)^(-(t - 1))

    @variable(m, X[1:nE, 1:NT] >= 0)
    @variable(m, Q[1:nE, 1:Lmax, 1:NT] >= 0)
    @variable(m, S[1:nE, 1:NT] >= 0)
    @variable(m, I[1:nE, 1:NT] >= 0)
    @variable(m, R[1:nE, 1:NT] >= 0)
    @variable(m, G[1:nE, 1:NT] >= 0)
    @variable(m, Z[1:NT] >= 0)

    # initial conditions, matching the SDDP state initial_values exactly
    @constraint(m, [e = 1:nE], X[e, 1] == E.xinit[e])
    @constraint(m, [e = 1:nE, k = 1:Lmax], Q[e, k, 1] == E.qinit[e, k])   # Koysha pipeline seeded here
    @constraint(m, [e = 1:nE], S[e, 1] == 0.0)

    # ---- annual energy balance (named for introspection: RHS is the realized/expected demand) ----
    @constraint(m, bal[t = 1:NT], sum(G[e, t] for e in 1:nE) + Z[t] == demand[t])
    # ---- reservoir hydro with realized/expected multiplier: G_hy <= eta_hy mu_t X_hy ----
    @constraint(m, hyd[t = 1:NT], G[hi, t] <= E.eta[hi] * muvec[t] * X[hi, t])

    for t in 1:NT
        # ---- non-hydro annual availability: G_e <= eta_e X_e ----
        for e in 1:nE
            e != hi && @constraint(m, G[e, t] <= E.eta[e] * X[e, t])
        end
        # ---- retirement of inherited fleet + mandatory floor + ceiling ----
        for e in 1:nE
            @constraint(m, R[e, t] <= E.xinit[e] - S[e, t])
            @constraint(m, S[e, t] + R[e, t] >= E.accum[t, e])
            @constraint(m, X[e, t] <= E.ub[e])
            if E.fix_retire                                   # coal-exit "fixed" (inactive for baseline)
                @constraint(m, S[e, t] + R[e, t] <= E.accum[t, e] + 1e-6)
            end
        end
        # ---- annual emissions cap (no cumulative budget) ----
        if E.cap !== nothing
            @constraint(m, sum((E.EM[e] / 1e9) * G[e, t] for e in 1:nE) <= E.cap[t] / 1e9)
        end
        # ---- capacity / pipeline / retirement dynamics ----
        if t < NT
            for e in 1:nE
                L = E.lead[e]
                @constraint(m, X[e, t+1] == X[e, t] + Q[e, 1, t] + (L == 1 ? I[e, t] : 0.0) - R[e, t])
                for k in 1:Lmax-1
                    @constraint(m, Q[e, k, t+1] == Q[e, k+1, t] + (k == L - 1 ? I[e, t] : 0.0))
                end
                @constraint(m, Q[e, Lmax, t+1] == (Lmax == L - 1 ? I[e, t] : 0.0))
                @constraint(m, S[e, t+1] == S[e, t] + R[e, t])
            end
        end
    end
    if fixI !== nothing
        @constraint(m, [e = 1:nE, t = 1:NT], I[e, t] == fixI[e, t])
    end

    # objective: same planning cost as the SDDP stage objective (baseline: tau=0, no
    # refurb, no capacity slack). refurb term kept for parity on coal-exit scenarios.
    obj = @expression(m, sum(disc(t) * (
            sum((E.a1[e] + E.tau * E.EM[e]) * G[e, t] for e in 1:nE) +
            sum(E.a2[t, e] * I[e, t] for e in 1:nE) +
            sum(E.b1[e] * X[e, t] for e in 1:nE) +
            E.voll * Z[t] +
            ((E.refurb_coal > 0 && coal_idx !== nothing) ?
                E.refurb_coal * (E.xinit[coal_idx] - S[coal_idx, t]) : 0.0)
        ) for t in 1:NT) / MONEY)
    if E.salv_frac > 0
        # Salvage on new-build capacity standing at end of 2050, matching the SDDP
        # X.out convention (start-of-year stock plus 2050 commissioning), net of the sunk fleet.
        obj -= disc(NT) * E.salv_frac * sum(E.a2[NT, e] *
            (X[e, NT] + Q[e, 1, NT] + (E.lead[e] == 1 ? I[e, NT] : 0.0) - E.xinit[e] + S[e, NT]) for e in 1:nE) / MONEY
    end
    @objective(m, Min, obj)
    return m, I
end

solve_cost(m) = (optimize!(m); objective_value(m) * MONEY)

# Economic (resource) cost ($B), expected unserved (TWh), total objective ($B),
# salvage ($B), and discounted unserved (TWh) of a solved deterministic model.
function det_metrics(E::Eff, m)
    NT, nE = E.NT, E.nE
    disc(t) = (1 + E.disc)^(-(t - 1))
    G = value.(m[:G]); I = value.(m[:I]); X = value.(m[:X]); Z = value.(m[:Z]); S = value.(m[:S]); Q = value.(m[:Q])
    econ = sum(disc(t) * (sum(E.a1[e] * G[e, t] for e in 1:nE) +
                          sum(E.a2[t, e] * I[e, t] for e in 1:nE) +
                          sum(E.b1[e] * X[e, t] for e in 1:nE)) for t in 1:NT) / 1e9
    eue = sum(Z) / 1e6
    salv = E.salv_frac > 0 ? disc(NT) * E.salv_frac * sum(E.a2[NT, e] *
        (X[e, NT] + Q[e, 1, NT] + (E.lead[e] == 1 ? I[e, NT] : 0.0) - E.xinit[e] + S[e, NT]) for e in 1:nE) / 1e9 : 0.0
    discunmet = sum(disc(t) * Z[t] for t in 1:NT) / 1e6   # discounted TWh, for VoLL re-pricing
    return econ, eue, objective_value(m) * MONEY / 1e9, salv, discunmet
end

# ----------------------------------------------------------------------------
# Sample realized paths (demand deviation + hydrological condition) on the CONTINUOUS
# law: the AR(1) demand innovations and the country-specific Markov hydrology,
# started from hInit -- identical to the stochastic model's uncertainty.
# ----------------------------------------------------------------------------
function sample_paths(E::Eff, K::Int, seed::Int)
    rng = MersenneTwister(seed)
    D = Vector{Vector{Float64}}(undef, K); MU = Vector{Vector{Float64}}(undef, K)
    REG = Vector{Vector{Int}}(undef, K);   OM = Vector{Vector{Float64}}(undef, K)
    cP = [cumsum(E.hP[i, :]) for i in 1:3]; cInit = cumsum(E.hInit)
    for k in 1:K
        u = 0.0; d = zeros(E.NT); mu = zeros(E.NT); rg = zeros(Int, E.NT); om = zeros(E.NT)
        reg = searchsortedfirst(cInit, rand(rng))
        for t in 1:E.NT
            d[t] = E.central[t] + u
            mu[t] = E.mu[reg]; rg[t] = reg
            incr = E.sigma_frac * E.central[t] * randn(rng)   # demand innovation at SDDP stage t (u.out = rho*u.in + incr)
            om[t] = incr
            u = E.rho * u + incr
            reg = searchsortedfirst(cP[reg], rand(rng))
        end
        D[k] = d; MU[k] = mu; REG[k] = rg; OM[k] = om
    end
    return D, MU, REG, OM
end

# Replay the trained SDDP policy on a prescribed path (condition sequence, demand
# innovations) and return resource cost, unserved, total, salvage, discounted unserved.
function sddp_path_metrics(model, E::Eff, reg::Vector{Int}, om::Vector{Float64})
    scenario = [((t, reg[t]), om[t]) for t in 1:E.NT]
    path = SDDP.simulate(model, 1, [:X, :G, :I, :Z, :S];
                         sampling_scheme = SDDP.Historical(scenario))[1]
    disc(t) = (1 + E.disc)^(-(t - 1))
    total = sum(d[:stage_objective] for d in path) * MONEY / 1e9
    econ = 0.0; eue = 0.0; discunmet = 0.0
    for (t, d) in enumerate(path)
        G = d[:G]; I = d[:I]; X = d[:X]
        econ += disc(t) * (sum(E.a1[e] * G[e] for e in 1:E.nE) +
                           sum(E.a2[t, e] * I[e] for e in 1:E.nE) +
                           sum(E.b1[e] * X[e].in for e in 1:E.nE))   # fixed O&M on start-of-year stock (matches objective/build_det)
        zt = d[:Z]
        eue += zt; discunmet += disc(t) * zt
    end
    XN = path[E.NT][:X]; SN = path[E.NT][:S]
    salv = E.salv_frac > 0 ?
        disc(E.NT) * E.salv_frac * sum(E.a2[E.NT, e] * (XN[e].out - E.xinit[e] + SN[e].out) for e in 1:E.nE) / 1e9 : 0.0
    return econ / 1e9, eue / 1e6, total, salv, discunmet / 1e6
end

# Stage-wise expected hydrology multiplier under the Markov chain started from
# hInit:  E[mu_t] = (hInit' P^{t-1}) . mu . This is the deterministic expected-
# hydrology representation. It respects a known initial condition (e.g. a low-
# inflow start, hInit=[1,0,0]) instead of hard-coding the long-run mean. It converges to
# the stationary mean sum_h pi[h] mu[h] = 1 as t grows.
function expected_mu_path(E::Eff)
    dist = copy(E.hInit)
    mean_mu = zeros(E.NT)
    for t in 1:E.NT
        mean_mu[t] = dot(dist, E.mu)
        dist = E.hP' * dist          # advance the condition distribution one year
    end
    return mean_mu
end

# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------
function run_benchmarks(cc; K = 200, seed = 20240807, outdir = joinpath(@__DIR__, "..", "results", cc))
    P = JSON.parsefile(joinpath(@__DIR__, "..", "data", "params_$(cc).json"))
    scn = first(s for s in P["scenarios"] if s["name"] == "baseline")
    E = prep_scenario(P, scn)

    # 1) Train the SDDP policy with the SAME production settings; it is scored below
    # on the SAME continuous paths as the EV and PI plans.
    model = build_sddp(E)
    Random.seed!(12345)                              # reproducible forward-pass sampling
    SDDP.train(model; iteration_limit = 200, print_level = 1,
               stopping_rules = [RobustGapUpper(rel_tol = 0.03, min_iter = 30,
                                                stable_needed = 5, m_ub = 500)])

    # 2) Deterministic expected-value plan: demand at its expectation (E[u_t]=0 so
    # E[demand_t]=central[t]) and hydrology at the stage-wise expected multiplier.
    mean_mu = expected_mu_path(E)
    mEV, Ivar = build_det(E, E.central, mean_mu)
    optimize!(mEV)
    Istar = value.(Ivar)

    # 3) Score all three plans on one common set of continuous-law paths, making VSS
    # and EVPI paired differences. PI is per-path optimal, so EVPI >= 0 by construction.
    D, MU, REG, OM = sample_paths(E, K, seed)
    sddp_e = zeros(K); sddp_u = zeros(K); sddp_t = zeros(K); sddp_s = zeros(K); sddp_du = zeros(K)
    ev_e = zeros(K); ev_u = zeros(K); ev_t = zeros(K); ev_s = zeros(K); ev_du = zeros(K)
    pi_e = zeros(K); pi_u = zeros(K); pi_t = zeros(K); pi_s = zeros(K); pi_du = zeros(K)
    for k in 1:K
        sddp_e[k], sddp_u[k], sddp_t[k], sddp_s[k], sddp_du[k] = sddp_path_metrics(model, E, REG[k], OM[k])
        mfix, _ = build_det(E, D[k], MU[k]; fixI = Istar); optimize!(mfix)          # EEV: EV plan operated on path k
        ev_e[k], ev_u[k], ev_t[k], ev_s[k], ev_du[k] = det_metrics(E, mfix)
        mfree, _ = build_det(E, D[k], MU[k]);             optimize!(mfree)          # PI: path k fully known
        pi_e[k], pi_u[k], pi_t[k], pi_s[k], pi_du[k] = det_metrics(E, mfree)
    end
    sddp_econ = mean(sddp_e); sddp_eue = mean(sddp_u); sddp_total = mean(sddp_t)
    sddp_salv = mean(sddp_s); sddp_discunmet = mean(sddp_du)

    # Paired differences on the common paths (variance-reduced, no law mismatch).
    vss_path  = ev_t .- sddp_t                        # value of adaptivity, per path (EEV - SDDP)
    evpi_path = sddp_t .- pi_t                        # value of perfect information, per path (>= 0 by optimality of PI)
    ci(v) = 1.96 * std(v) / sqrt(length(v))           # paired 95% CI half-width
    function boot_ci(v; B = 4000, bseed = 97)         # 95% percentile paired bootstrap
        rng = MersenneTwister(bseed); n = length(v); m = zeros(B)
        for b in 1:B
            s = 0.0; for _ in 1:n; s += v[rand(rng, 1:n)]; end; m[b] = s / n
        end
        sort!(m); return (m[max(1, round(Int, 0.025B))], m[round(Int, 0.975B)])
    end
    vss_lo, vss_hi = boot_ci(vss_path); evpi_lo, evpi_hi = boot_ci(evpi_path)
    vss_at(x) = mean(vss_path .+ (x - E.voll) .* (ev_du .- sddp_du) .* 1e-3)   # VSS re-priced at VoLL = x, paired
    out = Dict(
        "country" => cc, "K" => K, "seed" => seed,
        # resource cost ($B): investment + O&M + fuel, the reliability-independent dimension
        "sddp_econ_BUSD" => sddp_econ, "ev_econ_BUSD" => mean(ev_e), "pi_econ_BUSD" => mean(pi_e),
        # expected unserved energy (TWh): the reliability dimension of adaptivity
        "sddp_eue_TWh" => sddp_eue, "ev_eue_TWh" => mean(ev_u), "pi_eue_TWh" => mean(pi_u),
        # total expected cost ($B), all three scored on the common continuous law
        "sddp_total_BUSD" => sddp_total, "ev_total_BUSD" => mean(ev_t), "pi_total_BUSD" => mean(pi_t),
        "sddp_total_ci" => ci(sddp_t), "ev_total_ci" => ci(ev_t), "pi_total_ci" => ci(pi_t),
        # VSS = E[EEV - SDDP], EVPI = E[SDDP - PI], paired mean differences ($B)
        "VSS_total_BUSD" => mean(vss_path), "EVPI_total_BUSD" => mean(evpi_path),
        # paired inference: normal half-width and 95% percentile bootstrap interval
        "VSS_total_ci" => ci(vss_path), "VSS_total_boot_lo" => vss_lo, "VSS_total_boot_hi" => vss_hi,
        "EVPI_total_ci" => ci(evpi_path), "EVPI_total_boot_lo" => evpi_lo, "EVPI_total_boot_hi" => evpi_hi,
        # EEV-minus-PI paired on the common sample (low-noise total gap from perfect info)
        "ev_minus_pi_BUSD" => mean(ev_t .- pi_t), "ev_minus_pi_ci" => ci(ev_t .- pi_t),
        # VSS/EVPI on resource cost, plus the unserved-energy gaps
        "VSS_econ_pct" => 100 * (mean(ev_e) - sddp_econ) / sddp_econ,
        "VSS_eue_gap_TWh" => mean(ev_u) - sddp_eue,
        "EVPI_econ_pct" => 100 * (sddp_econ - mean(pi_e)) / sddp_econ,
        "EVPI_eue_gap_TWh" => sddp_eue - mean(pi_u),
        # VSS/EVPI on total cost, for reference
        "VSS_total_pct" => 100 * mean(vss_path) / sddp_total,
        "EVPI_total_pct" => 100 * mean(evpi_path) / sddp_total,
        # terminal salvage credit ($B): full objective = econ + VoLL shortage - salvage
        "sddp_salvage_BUSD" => sddp_salv, "ev_salvage_BUSD" => mean(ev_s), "pi_salvage_BUSD" => mean(pi_s),
        # VoLL-independent primary finding: avoided unserved energy (TWh)
        "avoided_eue_TWh" => mean(ev_u) - sddp_eue,
        # VSS on the total objective re-priced at three VoLL levels ($B), paired per path
        "VSS_total_at_10k" => vss_at(10000.0),
        "VSS_total_at_20k" => vss_at(20000.0),
        "VSS_total_at_40k" => vss_at(40000.0),
    )
    dir = outdir; mkpath(dir)
    open(joinpath(dir, "benchmarks.json"), "w") do io; JSON.print(io, out, 2); end
    @info "benchmarks $cc" VSS_econ=out["VSS_econ_pct"] EUE_gap=out["VSS_eue_gap_TWh"] EVPI_econ=out["EVPI_econ_pct"]
    return out
end

if abspath(PROGRAM_FILE) == @__FILE__
    cc = length(ARGS) >= 1 ? ARGS[1] : "ETH"
    od = length(ARGS) >= 2 ? ARGS[2] : joinpath(@__DIR__, "..", "results", cc)   # optional output dir (results/<CC>/benchmarks)
    run_benchmarks(cc; outdir = od)
end
