# Decisions

## D1: Synthetic data with injected defects
Context: no real company data is available.
Decision: generate NovaMart data with a fixed seed and inject 11 types of data problems, logged in a manifest.
Why: the pipeline and validation can be tested against known problems, and there is no privacy risk.
Trade-off: synthetic patterns are simpler than real ones, so model scores will look better than in reality.