###############################################################################
# benchmarks.jl  --  Value of adaptivity (VSS) and information (EVPI)
#
# For a country baseline (demand + hydrology uncertainty):
#   SDDP policy      : train, simulate out of sample (from gep_sddp.jl)
#   Deterministic EV : solve on mean demand + expected hydrology (stationary mean) -> fix investment,
#                      evaluate that fixed schedule out of sample (dispatch adapts)
#   Perfect info     : solve each realized path with the future known
#   VSS  = E[cost | EV]  - E[cost | SDDP]
#   EVPI = E[cost | SDDP] - E[cost | perfect info]
#
# Usage:  julia --project=. benchmarks.jl ETH
###############################################################################

include("gep_sddp.jl")   # brings prep_scenario, build_sddp, simulate_summary, Eff, GRB_ENV, MONEY into scope

# ----------------------------------------------------------------------------
# Deterministic multistage LP (JuMP + Gurobi) over a given realized path
# ----------------------------------------------------------------------------
function build_det(E::Eff, demand::Vector{Float64}, muvec::Vector{Float64};
                   fixI = nothing)
    nE, nB, Lmax, NT = E.nE, E.nB, E.Lmax, E.NT
    coal_idx = findfirst(==("Coal"), E.tech)
    m = Model(() -> Gurobi.Optimizer(GRB_ENV)); set_silent(m)
    disc(t) = (1 + E.disc)^(-(t - 1))

    @variable(m, X[1:nE, 1:NT] >= 0)
    @variable(m, Q[1:nE, 1:Lmax, 1:NT] >= 0)
    @variable(m, S[1:nE, 1:NT] >= 0)
    @variable(m, I[1:nE, 1:NT] >= 0)
    @variable(m, R[1:nE, 1:NT] >= 0)
    @variable(m, G[1:nE, 1:nB, 1:NT] >= 0)
    @variable(m, Z[1:nB, 1:NT] >= 0)
    @variable(m, rshort[1:NT] >= 0)

    @constraint(m, [e = 1:nE], X[e, 1] == E.xinit[e])
    @constraint(m, [e = 1:nE, k = 1:Lmax], Q[e, k, 1] == 0.0)
    @constraint(m, [e = 1:nE], S[e, 1] == 0.0)

    for t in 1:NT
        @constraint(m, [b = 1:nB],
            sum(G[e, b, t] for e in 1:nE) + Z[b, t] == E.dshare[b] * demand[t])
        for e in 1:nE, b in 1:nB
            if e != E.hydro_idx
                @constraint(m, G[e, b, t] <= E.cf[e, b] * E.H[b] * X[e, t])
            end
        end
        # reservoir hydro: turbine power limit + seasonal water budgets
        hi = E.hydro_idx
        @constraint(m, [b = 1:nB], G[hi, b, t] <= E.hydro_avail * E.H[b] * X[hi, t])
        @constraint(m, sum(G[hi, b, t] for b in 1:nB if E.season[b] == 1) <= muvec[t] * E.water_wet * E.alpha[hi] * X[hi, t])
        @constraint(m, sum(G[hi, b, t] for b in 1:nB if E.season[b] == 2) <= muvec[t] * E.water_dry * E.alpha[hi] * X[hi, t])
        @constraint(m, sum(E.cap_credit[e] * X[e, t] for e in 1:nE) + rshort[t] >=
                       (1 + E.reserve_margin) * E.peak_factor * demand[t])
        for e in 1:nE
            @constraint(m, R[e, t] <= E.xinit[e] - S[e, t])
            @constraint(m, S[e, t] + R[e, t] >= E.accum[t, e])
            @constraint(m, X[e, t] <= E.ub[e])
        end
        if E.cap !== nothing
            @constraint(m, sum((E.EM[e] / 1e9) * G[e, b, t] for e in 1:nE, b in 1:nB) <= E.cap[t] / 1e9)
        end
        if t < NT
            for e in 1:nE
                L = E.lead[e]
                # construction pipeline: commission after exactly L years
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

    gen(e, t) = sum(G[e, b, t] for b in 1:nB)
    obj = @expression(m, sum(disc(t) * (
            sum((E.a1[e] + E.tau * E.EM[e]) * gen(e, t) for e in 1:nE) +
            sum(E.a2[t, e] * I[e, t] for e in 1:nE) +
            sum(E.b1[e] * X[e, t] for e in 1:nE) +
            E.voll * sum(Z[b, t] for b in 1:nB) +
            E.cap_short_price * rshort[t]) for t in 1:NT) / MONEY)
    if E.salv_frac > 0
        # salvage only on NEW-BUILD capacity still standing at the horizon end,
        # X - (xinit - S), so the inherited pre-2025 fleet (sunk cost) gets no credit.
        obj -= disc(NT) * E.salv_frac * sum(E.a2[NT, e] * (X[e, NT] - E.xinit[e] + S[e, NT]) for e in 1:nE) / MONEY
    end
    @objective(m, Min, obj)
    return m, I
end

solve_cost(m) = (optimize!(m); objective_value(m) * MONEY)

# Economic cost ($B), expected unserved (TWh), and total objective ($B) of a solved det model
function det_metrics(E::Eff, m)
    NT, nE, nB = E.NT, E.nE, E.nB
    disc(t) = (1 + E.disc)^(-(t - 1))
    G = value.(m[:G]); I = value.(m[:I]); X = value.(m[:X]); Z = value.(m[:Z]); S = value.(m[:S])
    gy(e, t) = sum(G[e, b, t] for b in 1:nB)
    econ = sum(disc(t) * (sum(E.a1[e] * gy(e, t) for e in 1:nE) +
                          sum(E.a2[t, e] * I[e, t] for e in 1:nE) +
                          sum(E.b1[e] * X[e, t] for e in 1:nE)) for t in 1:NT) / 1e9
    eue = sum(Z) / 1e6
    salv = E.salv_frac > 0 ? disc(NT) * E.salv_frac * sum(E.a2[NT, e] * (X[e, NT] - E.xinit[e] + S[e, NT]) for e in 1:nE) / 1e9 : 0.0
    discunmet = sum(disc(t) * sum(Z[b, t] for b in 1:nB) for t in 1:NT) / 1e6   # discounted TWh, for VoLL re-pricing
    return econ, eue, objective_value(m) * MONEY / 1e9, salv, discunmet
end

# ----------------------------------------------------------------------------
# Sample realized paths (demand deviation + hydrology regime)
# ----------------------------------------------------------------------------
function sample_paths(E::Eff, K::Int, seed::Int)
    rng = MersenneTwister(seed)
    D = Vector{Vector{Float64}}(undef, K); MU = Vector{Vector{Float64}}(undef, K)
    cP = [cumsum(E.hP[i, :]) for i in 1:3]; cInit = cumsum(E.hInit)
    for k in 1:K
        u = 0.0; d = zeros(E.NT); mu = zeros(E.NT)
        reg = searchsortedfirst(cInit, rand(rng))
        for t in 1:E.NT
            d[t] = E.central[t] + u
            mu[t] = E.mu[reg]
            u = E.rho * u + E.sigma_frac * E.central[t] * randn(rng)
            reg = searchsortedfirst(cP[reg], rand(rng))
        end
        D[k] = d; MU[k] = mu
    end
    return D, MU
end

# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------
function run_benchmarks(cc; K = 200, seed = 20240807)
    P = JSON.parsefile(joinpath(@__DIR__, "params_$(cc).json"))
    scn = first(s for s in P["scenarios"] if s["name"] == "baseline")
    E = prep_scenario(P, scn)

    # 1) SDDP policy, evaluated out of sample. Economic cost + EUE from mean trajectory.
    model = build_sddp(E)
    Random.seed!(12345)                              # reproducible forward-pass sampling
    SDDP.train(model; iteration_limit = 200, print_level = 1,
               stopping_rules = [RobustGapUpper(rel_tol = 0.03, min_iter = 30,
                                                stable_needed = 5, m_ub = 500)])
    oos, sims = simulate_summary(model, E; N = K, seeds = [seed])
    cap, genb, build, retire, unmet, _ = mean_trajectories(sims, E)
    genyr = dropdims(sum(genb, dims = 3), dims = 3)
    disc(t) = (1 + E.disc)^(-(t - 1))
    sddp_econ = sum(disc(t) * (sum(E.a1[e] * genyr[e, t] for e in 1:E.nE) +
                               sum(E.a2[t, e] * build[e, t] for e in 1:E.nE) +
                               sum(E.b1[e] * cap[e, t] for e in 1:E.nE)) for t in 1:E.NT) / 1e9
    sddp_eue = sum(unmet) / 1e6
    sddp_total = oos["oos_cost_BUSD_mean"]
    sddp_salv = E.salv_frac > 0 ? disc(E.NT) * E.salv_frac * sum(E.a2[E.NT, e] * (cap[e, E.NT] - E.xinit[e] + sum(retire[e, :])) for e in 1:E.nE) / 1e9 : 0.0
    sddp_discunmet = sum(disc(t) * unmet[t] for t in 1:E.NT) / 1e6

    # 2) Deterministic expected-value plan (mean demand, EXPECTED hydrology) -> fixed schedule
    #    The expected-value problem fixes each uncertain input at its expectation. For
    #    hydrology this is the stationary-probability-weighted mean multiplier
    #    (= 1 by the normalization of E.mu), not the normal-regime value E.mu[2].
    pistat = Float64.(P["hydro"]["stationary"])
    exp_mu = dot(pistat, E.mu)                       # expected water multiplier
    mean_mu = fill(exp_mu, E.NT)
    mEV, Ivar = build_det(E, E.central, mean_mu)
    optimize!(mEV)
    Istar = value.(Ivar)

    # 3) Out-of-sample evaluation: EV (fixed I) and perfect information (free I) per path
    D, MU = sample_paths(E, K, seed)
    ev_e = zeros(K); ev_u = zeros(K); ev_t = zeros(K); ev_s = zeros(K); ev_du = zeros(K)
    pi_e = zeros(K); pi_u = zeros(K); pi_t = zeros(K); pi_s = zeros(K); pi_du = zeros(K)
    for k in 1:K
        mfix, _ = build_det(E, D[k], MU[k]; fixI = Istar); optimize!(mfix)
        ev_e[k], ev_u[k], ev_t[k], ev_s[k], ev_du[k] = det_metrics(E, mfix)
        mfree, _ = build_det(E, D[k], MU[k]);             optimize!(mfree)
        pi_e[k], pi_u[k], pi_t[k], pi_s[k], pi_du[k] = det_metrics(E, mfree)
    end

    ci(v) = 1.96 * std(v) / sqrt(length(v))          # 95% CI half-width (common random numbers for EV/PI)
    out = Dict(
        "country" => cc, "K" => K, "seed" => seed,
        # economic cost ($B): the value of adaptivity net of reliability
        "sddp_econ_BUSD" => sddp_econ, "ev_econ_BUSD" => mean(ev_e), "pi_econ_BUSD" => mean(pi_e),
        # expected unserved energy (TWh): the reliability dimension of adaptivity
        "sddp_eue_TWh" => sddp_eue, "ev_eue_TWh" => mean(ev_u), "pi_eue_TWh" => mean(pi_u),
        # total expected cost incl. VoLL ($B) with 95% confidence intervals
        "sddp_total_BUSD" => sddp_total, "ev_total_BUSD" => mean(ev_t), "pi_total_BUSD" => mean(pi_t),
        "sddp_total_ci" => get(oos, "oos_cost_BUSD_ci", NaN), "ev_total_ci" => ci(ev_t), "pi_total_ci" => ci(pi_t),
        # VSS = E[EV]-E[SDDP], EVPI = E[SDDP]-E[PI] on the total objective ($B)
        "VSS_total_BUSD" => mean(ev_t) - sddp_total, "EVPI_total_BUSD" => sddp_total - mean(pi_t),
        # EV-minus-PI is paired on the common sample (low-noise total gap from perfect info)
        "ev_minus_pi_BUSD" => mean(ev_t .- pi_t), "ev_minus_pi_ci" => ci(ev_t .- pi_t),
        # VSS/EVPI on economic cost, plus the unserved-energy gap
        "VSS_econ_pct" => 100 * (mean(ev_e) - sddp_econ) / sddp_econ,
        "VSS_eue_gap_TWh" => mean(ev_u) - sddp_eue,
        "EVPI_econ_pct" => 100 * (sddp_econ - mean(pi_e)) / sddp_econ,
        "EVPI_eue_gap_TWh" => sddp_eue - mean(pi_u),
        # VSS/EVPI on total cost (incl. VoLL), for reference
        "VSS_total_pct" => 100 * (mean(ev_t) - sddp_total) / sddp_total,
        "EVPI_total_pct" => 100 * (sddp_total - mean(pi_t)) / sddp_total,
        # terminal salvage credit ($B): full objective = econ + VoLL shortage + reserve - salvage
        "sddp_salvage_BUSD" => sddp_salv, "ev_salvage_BUSD" => mean(ev_s), "pi_salvage_BUSD" => mean(pi_s),
        # avoided unserved energy (TWh), the VoLL-independent primary finding
        "avoided_eue_TWh" => mean(ev_u) - sddp_eue,
        # VSS on the total objective at three VoLL levels ($B), re-pricing the fixed plans
        "VSS_total_at_10k" => (mean(ev_t) - sddp_total) + (10000.0 - E.voll) * (mean(ev_du) - sddp_discunmet) * 1e-3,
        "VSS_total_at_20k" => (mean(ev_t) - sddp_total) + (20000.0 - E.voll) * (mean(ev_du) - sddp_discunmet) * 1e-3,
        "VSS_total_at_40k" => (mean(ev_t) - sddp_total) + (40000.0 - E.voll) * (mean(ev_du) - sddp_discunmet) * 1e-3,
    )
    dir = joinpath(@__DIR__, "results", cc); mkpath(dir)
    open(joinpath(dir, "benchmarks.json"), "w") do io; JSON.print(io, out, 2); end
    @info "benchmarks $cc" VSS_econ=out["VSS_econ_pct"] EUE_gap=out["VSS_eue_gap_TWh"] EVPI_econ=out["EVPI_econ_pct"]
    return out
end

if abspath(PROGRAM_FILE) == @__FILE__
    run_benchmarks(length(ARGS) >= 1 ? ARGS[1] : "ETH")
end
