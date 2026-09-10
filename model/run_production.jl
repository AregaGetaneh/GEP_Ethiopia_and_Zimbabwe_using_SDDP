# run_production.jl -- solve a scenario group or all scenarios for one country.
# Usage: julia --project=. model/run_production.jl <CC> [group|scenario|all] [outbase]
using SHA, Dates, Pkg
include(joinpath(@__DIR__, "gep_sddp.jl"))

# scenario_id -> policy-family subfolder (keeps results/ organized)
function group_of(cc, name)
    name == "baseline" && return "baseline"
    if cc == "ZWE"
        name in ("carbon_tax_30", "carbon_tax_50")                       && return "carbon_tax"
        name in ("cap_matched_30", "cap_matched_50", "emission_cap_glide") && return "annual_cap"
        startswith(name, "coal_retire")                                   && return "coal_exit"
    end
    return "scenarios"
end

pkg_version(name) = try
    string(first(v.version for (_, v) in Pkg.dependencies() if v.name == name))
catch
    "n/a"
end

function provenance(cc, scn, paramfile, resultpath, itcap)
    meta = JSON.parsefile(resultpath)["meta"]
    niter = get(meta, "n_iter", -1)
    term = niter < 0 ? "unknown" : (niter < itcap ? "converged (robust_gap_upper)" : "iteration_limit")
    return Dict(
        "scenario_id"     => scn["name"],
        "country"         => cc,
        "group"           => group_of(cc, scn["name"]),
        "param_file"      => basename(paramfile),
        "param_sha256"    => bytes2hex(sha256(read(paramfile))),
        "start_time"      => string(now()),
        "julia_version"   => string(VERSION),
        "gurobi_version"  => pkg_version("Gurobi"),
        "sddp_version"    => pkg_version("SDDP"),
        "train_seed"      => 12345,
        "sim_seeds"       => [1, 2, 3, 4, 5],
        "stopping_rule"   => "RobustGapUpper(rel_tol=0.03, min_iter=30, stable_needed=5, m_ub=500)",
        "iteration_limit" => itcap,
        "final_bound_BUSD" => get(meta, "lower_bound_BUSD", NaN),
        "n_iter"          => niter,
        "wall_s"          => get(meta, "wall_s", NaN),
        "termination"     => term,
    )
end

function main_production()
    cc      = length(ARGS) >= 1 ? ARGS[1] : "ETH"
    filt    = length(ARGS) >= 2 ? ARGS[2] : "all"
    outbase = length(ARGS) >= 3 ? ARGS[3] : joinpath(@__DIR__, "..", "results")
    force   = get(ENV, "GEP_FORCE", "0") == "1"
    paramfile = joinpath(@__DIR__, "..", "data", "params_$(cc).json")
    P = JSON.parsefile(paramfile)
    itcap_default = 200

    groups = ("baseline", "carbon_tax", "annual_cap", "coal_exit", "scenarios")
    want(name) = filt == "all" || filt == name || (filt in groups && group_of(cc, name) == filt)

    refpaths = Dict{String,Vector{Float64}}()          # tax ref -> expected annual emissions path (Mt)
    annual_emis(respath) = Float64.(JSON.parsefile(respath)["emissions_Mt"])   # Mt per year

    for scn in P["scenarios"]                         # file order preserves match_emissions_of dependencies
        name = scn["name"]
        grp  = group_of(cc, name)
        outdir = joinpath(outbase, cc, grp)
        out = joinpath(outdir, "$(name).json")

        # only touch scenarios in the requested group/filter (guard BEFORE any ref work,
        # so e.g. GRP=baseline does not pull in a matched scenario's tax reference)
        if !want(name)
            continue
        end

        # emissions-matched annual cap: the cap TRAJECTORY equals the reference tax
        # scenario's expected annual emissions path (feasible every year, matches year
        # by year). Fetch that path first, reusing an existing result or running inline.
        ref = get(scn, "match_emissions_of", nothing)
        if ref !== nothing && !haskey(refpaths, ref)
            refout = joinpath(outbase, cc, group_of(cc, ref), "$(ref).json")
            if isfile(refout) && !force
                refpaths[ref] = annual_emis(refout)
                @info "using existing $ref annual emissions path for trajectory match"
            else
                @warn "scenario $name needs reference $ref; computing it inline"
                refscn = first(s for s in P["scenarios"] if s["name"] == ref)
                _, _, refpath = run_scenario(P, refscn; outdir = joinpath(outbase, cc, group_of(cc, ref)))
                refpaths[ref] = refpath
            end
        end

        if isfile(out) && !force
            @info "skip $name (exists; set GEP_FORCE=1 to overwrite): $out"
            continue
        end

        extra = Dict{String,Any}()
        if ref !== nothing
            # cap_t = lambda * E[EM_t^tax], lambda >= 1 calibrated so the cap's EXPECTED
            # CUMULATIVE emissions match the tax's (a cap at the mean path under-emits,
            # since it trims high realizations; lambda loosens it upward). Calibrated on
            # the trial seed set [201..205]; the scenario is then evaluated on production
            # seeds [1..5] -- a separate sample, so the reported match is out-of-sample.
            path_Mt = refpaths[ref]
            target  = sum(path_Mt)
            capvec(l) = [l * e * 1.0e9 for e in path_Mt]
            emis_of(l) = trial_emissions(P, scn, capvec(l))            # cumulative Mt on trial seeds
            lam_lo, lam_hi = 1.0, 3.0
            f_lo = emis_of(lam_lo) - target
            f_hi = emis_of(lam_hi) - target
            tries = 0
            while f_hi < 0 && tries < 3                                # widen until the bracket spans target
                lam_hi *= 1.5; f_hi = emis_of(lam_hi) - target; tries += 1
            end
            lam = lam_lo
            if f_lo < 0                                                # normal case: bisect for the match
                for _ in 1:14
                    lam = 0.5 * (lam_lo + lam_hi)
                    f = emis_of(lam) - target
                    abs(f) / target <= 0.005 && break                  # calibrate to <=0.5% on the trial seeds
                    f < 0 ? (lam_lo = lam; f_lo = f) : (lam_hi = lam; f_hi = f)
                end
            end
            scn["emission_cap"] = capvec(lam)
            extra = Dict("matched_ref" => ref, "matched_mode" => "trajectory_lambda",
                         "matched_lambda" => lam, "matched_target_cum_Mt" => target)
            @info "matched-cap lambda calibrated" scenario=name lambda=lam target_Mt=target
        end
        scn["iteration_limit"] = get(scn, "iteration_limit", 700)     # production ceiling (keep 3% tolerance)
        itcap = Int(scn["iteration_limit"])
        _, oos, _ = run_scenario(P, scn; outdir = outdir, extra = extra)

        # provenance sidecar (+ matched-cap diagnostic: achieved vs tax cumulative emissions, OOS)
        prov = provenance(cc, scn, paramfile, out, itcap)
        if ref !== nothing
            target = sum(refpaths[ref]); achieved = oos["oos_emis_Mt_mean"]
            prov["matched_ref"] = ref
            prov["matched_mode"] = "trajectory_lambda"
            prov["matched_lambda"] = get(extra, "matched_lambda", NaN)
            prov["matched_target_cum_Mt"] = target
            prov["matched_achieved_cum_Mt"] = achieved
            prov["matched_pct_diff"] = 100 * (achieved - target) / target
            @info "matched-cap diagnostic $name" target=target achieved=achieved pct=prov["matched_pct_diff"]
        end
        open(joinpath(outdir, "$(name).meta.json"), "w") do io; JSON.print(io, prov, 2); end
        @info "provenance written for $name" bound=prov["final_bound_BUSD"] iters=prov["n_iter"] term=prov["termination"]
    end
end

if abspath(PROGRAM_FILE) == @__FILE__
    main_production()
end
