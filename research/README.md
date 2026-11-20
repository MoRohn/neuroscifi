# NeuroSciFi empirical evidence

This bundle reproduces the analyses in the NeuroSciFi architecture brief. It contains the analysis code, all reported aggregate outputs, and the underlying CPU timing observations. The research inputs are retrieved from their authors using pinned commits and verified Git blob hashes. Use the original sources' access and license terms. No participant recordings or person-level records are included in this bundle.

## Run the analyses

Python 3.12.14, NumPy 2.3.5 and SciPy 1.17.0 were used. The scripts require NumPy and SciPy. An internet connection to raw.githubusercontent.com is needed for the initial input download. Each file's repository, commit, path and Git blob ID are in inputs.json. Supply those files under inputs/ to run offline. The helper verifies their hashes.

```sh
bash scripts/run_analyses.sh
python3 scripts/summarize_timings.py results
```

The first command writes fresh numerical results into reproduced/. The second prints the timing summaries from the original test host. To run the actual kernels on another Linux host with a C++17 compiler, use the command below. Choose a CPU number available in the process affinity mask, or omit BENCH_CORE for unpinned execution.

```sh
BENCH_CORE=2 bash scripts/run_cpu.sh
python3 scripts/summarize_timings.py reproduced
```

Platform-specific timings will vary. These tests profile CPU update layout. Test the ARM CPU, shared-memory contention, GPU kernels, full-device latency and wall energy on the target Spark.

## Map from evidence to results

| Analysis | Input and evaluation | Result |
|---|---|---|
| Receptor compression | 100 cortical regions and 19 receptor/transporter channels. Standardize from each training fold. Hold out each hemisphere or each of seven functional networks. | atlas.json |
| Receptor sensitivity | Withhold every channel belonging to a transmitter family in the held-out network. Compute conditional principal-component estimates and a direct ridge baseline. Sweep four regularization settings. | atlas_sensitivity.json |
| Timing contrasts | 64 participants per online experiment. Welch intervals compare the groups using the study authors’ participant-level summaries. | timing.json |
| Pre-movement prediction | Two groups, eight held-participant folds per group. Each person contributes equally to fitting and evaluation. Aggregate predictions from 41,498 short-interval and 41,473 long-interval valid trials. | forecast.json |
| Two-device placement | 100-node group structural and functional matrices. Remove diagonal self-connections. Functional weights are clipped at zero. Compare 2,000 random balanced placements and deterministic local search with multiple starts. | placement.json |
| Associative learning | 40 development worlds per family, then 192 fresh final test worlds per family. Outcomes include old-context error, new-context error, unseen-combination error and old-context forgetting. | association_final.json |
| Context reliability | Separate set of 192 worlds per family. Only familiar pairs scored. Evaluate hard context retrieval and a posterior-weighted blend under known context-label corruption. | context.json |
| Intervention selection | 4,096 simulated worlds, balanced truth tables by construction. Common random noise across policies. Exact Bayes with known 10% outcome noise. | intervention.json |
| Curiosity control | 4,096 worlds plus a fair-coin distractor with known observation distribution. Compare entropy-seeking, disagreement-adjusted information gain and random action selection. | curiosity.json |
| Precision | 256 scalar estimates, three target regimes, 2,048 updates per regime. Report pre-update error over the final 256 steps of each regime. | precision.json |
| CPU update layout | 4 MiB and 128 MiB connection arrays. Six active fractions, single-record or 64-record activation clusters. Five RNG seeds, 13 measured trials for each of four update paths, seven copy/sort trials per configuration. | bench_*.csv |

### How to interpret the numbers

**Spatial validation.** Using 19 observed channels to reconstruct the same observation measures compression. Predicting a withheld channel measures cross-channel prediction. The denominator is the total squared deviation from training-region means after training-only scaling. Masked tests use the held-in channels of test regions to estimate latent coordinates. The matrix is group-averaged and spatially correlated. Independent donor cohorts were unavailable. The receptor-family sensitivity comparison is aggregate and channel-weighted, so larger families contribute more channel predictions.

**Behavioral prediction.** The three model columns in forecast.json use time and behavior history, then add previous feedback, then the current cue with a phase-specific coefficient. Features include previous valid heading, two causal exponential averages, task phase and simple transition transients. CS+ orientation comes from the experiment design. The model is estimating expression of a cue given its identity. It is not a from-scratch sensory learner. Bootstrap intervals resample held-out participants conditional on fitted predictions. They exclude uncertainty from refitting the whole procedure.

**Synthetic association.** Context identifiers and discrete modality tokens are supplied. The first three numbers under each family are old-context seen MSE, new-context seen MSE, and mean unseen-pair MSE across both contexts. The fourth is old-context seen MSE after learning context B minus old-context seen MSE before B. Tied and split use a shared residual at each step. Local uses a marginal-predictor error for two modality weights and a full prediction residual for the context-specific pair weight. These algorithms are analytic controls, not spiking neuronal simulations. Fixed fallback strength and rates were selected using the mean of the three primary endpoints with equal family weights. Adaptive fallback estimates its shrinkage entirely from familiar pairs, omitting each pair and all its repeats in turn. The final test range was changed to fresh seeds after this variant was introduced. The exploratory regimes share random seeds to enable paired comparisons. Resource use differs across the table and neural-style algorithms.

**CPU timing.** Research run used g++ -O3 -march=native -std=c++17 on an x86-64 AMD EPYC 9V74 virtual host, pinned to one logical CPU. The full state fits neither in a single core's private cache nor inside the event stream. The large-array table aggregates each seed's trial medians, then takes the median across seeds. Kernel order is shuffled within a trial. Repeated calls reuse the same ID set. Masks and ready-ordered lists are prepared in advance, whereas the bitmap rebuilds every step. Copy/sort is separately timed with preallocated output. All active IDs are unique per tick. The optional verification build checks every record across all kernels against the same starting state to within 0.000002. Platform scheduling and cache history can affect the small timings. The test includes neither end-to-end sensor acquisition nor GPU execution.

### Source materials

- [Hansen et al. 2022 and authors’ datasets](https://github.com/netneurolab/hansen_receptors). Study data and code are licensed under CC BY-NC-SA 4.0 by the source repository.
- [Schaefer atlas labels, Thomas Yeo Lab CBIG](https://github.com/ThomasYeoLab/CBIG/tree/master/stable_projects/brain_parcellation/Schaefer2018_LocalGlobal).
- [Avraham et al. 2022 and authors’ data](https://github.com/guyavr/AssociativeMotorAdaptation). The two files under Codes/Simulations contain the cleaned empirical time series used for model fitting. We verified all 64 × 800 heading values against each corresponding DataAnalysis file, including missing-value positions. LongITI_Comb corresponds to the short cue-interval group; VaryingDelay_Comb corresponds to the longer cue interval.
