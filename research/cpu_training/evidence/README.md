# Archived CPU-training evidence

These files preserve measurements and the source snapshots actually executed.
Snapshots are historical artifacts, not the entry points for new experiments.
Use the scripts in the parent directory to run new experiments.

| Directory | Scope |
| --- | --- |
| [numa-v3](numa-v3/) | Original three-step MLP placement/correctness pilot |
| [e0-validated](e0-validated/) | Parameterized MLP and two Transformer correctness cases |
| [e1-diagnostic](e1-diagnostic/) | Eight DRAM/Direct/sync/async correctness and residency runs |
| [e2-timing](e2-timing/) | Forty independent timing processes, five paired repetitions per configuration |

The new archives are copied from the matching `results/cpu-training-*` directories.
They include per-run manifests, source snapshots, raw step/event data, summaries,
and available figures. Build/cache files are omitted; `.log` files are preserved
as `.log.txt` to avoid the repository's generated-log ignore rule. Commands and
absolute/local output paths retain their original values for provenance.
Every archived manifest's source hashes were checked after copying.

Read the [E1/E2 report](../E1_E2_REPORT.md) before interpreting timing results.
The async implementation uses eager FIFO scheduling and exhibits demand-order
inversion; its timings do not establish a benefit-aware policy or a comparison
against optimized Always-Prefetch/TierTrain. Diagnostic timings are not performance
measurements. Source snapshots may differ between experiment batches.
