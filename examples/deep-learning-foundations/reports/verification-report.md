# Verification report — Mathematical Foundations of Deep Learning

Generated 2026-11-17T20:12:47.028Z by ScholarForge 0.1.0.

> ScholarForge does not guarantee that every statement is true. This report lists exactly what was verified, by which method, and what remains unverified or needs expert review.

## Claims

| Status | Count |
|:--|--:|
| verified | 7 |
| supported | 13 |
| contested | 1 |
| unverified | 4 |
| requires expert review | 0 |

### Contested (1)

- [Neural Networks] *interpretation*: Why heavily overparameterised networks trained by stochastic gradient descent generalise well is still an open research question. — speculative / open question, labelled as such

### Unverified (4)

- [Calculus] *theorem*: If g is differentiable at a and f is differentiable at g(a), then (f o g)'(a) = f'(g(a)) g'(a). — proof given in the text; not machine-checked
- [Calculus] *empirical*: Small sigmoid derivatives multiply along deep networks, which makes deep sigmoid networks hard to train with gradient methods. — cited sources exist; no retrievable abstract or text to confirm the claim
- [Neural Networks] *historical*: Rumelhart, Hinton and Williams (1986) popularised backpropagation and showed that hidden units come to represent important features of the task domain. — cited sources exist; no retrievable abstract or text to confirm the claim
- [Neural Networks] *theorem*: Multilayer feedforward networks with a bounded nonconstant activation are universal approximators with respect to Lp(mu) criteria. — cited sources exist; no retrievable abstract or text to confirm the claim

## Sources

| Status | Count |
|:--|--:|
| verified | 12 |
| rejected | 1 |

Sources that are not citable:

- `doe2019` (rejected): Gradient Flows in Infinite-Width Transformers Are Always Convex — identifier does not resolve in any service (crossref:10.9999/sf-fabricated.2019.00123, openalex:10.9999/sf-fabricated.2019.00123, semantic_scholar:10.9999/sf-fabricated.2019.00123); the reference is probably fabricated or mistyped

## Mathematical checks

| Chapter | Check | Kind | Outcome | Method |
|:--|:--|:--|:--|:--|
| Calculus | sigmoid-derivative | derivative | pass | symbolic |
| Calculus | sigmoid-derivative-bound | inequality | pass | symbolic |
| Calculus | sigmoid-derivative-positive | inequality | pass | symbolic |
| Calculus | tanh-identity | identity | pass | numeric |
| Calculus | tanh-derivative | derivative | pass | symbolic |
| Linear Algebra | transpose-product | matrix_identity | pass | symbolic |
| Linear Algebra | quadratic-gradient | gradient | pass | symbolic |
| Linear Algebra | least-squares-gradient | derivative | pass | symbolic |
| Optimization | descent-quadratic-equality | identity | pass | symbolic |
| Optimization | gd-quadratic-step | identity | pass | symbolic |
| Optimization | telescoping-expansion | identity | pass | symbolic |
| Automatic Differentiation | ad-gradient | gradient | pass | symbolic |
| Automatic Differentiation | ad-exercise-gradient | gradient | pass | symbolic |
| Neural Networks | softmax-ce-z1 | identity | pass | symbolic |
| Neural Networks | softmax-ce-z2 | identity | pass | symbolic |
| Neural Networks | two-layer-w2 | derivative | pass | symbolic |
| Neural Networks | two-layer-w1 | derivative | pass | symbolic |
| Neural Networks | hat-peak | numeric | pass | symbolic |
| Neural Networks | hat-right | identity | pass | numeric |
| Neural Networks | hat-left | identity | pass | numeric |

## Figures

- fig-sigmoid: approved (matplotlib); review: ok
- fig-gd-paths: approved (matplotlib); review: ok
- fig-comp-graph: approved (matplotlib); review: ok

## Not verified by design

- Prose arguments and proofs written in natural language are not machine-checked unless a math check or a Lean proof is attached to them.
- Claim-support assessments are judgements of the host language model; only evidence quotes that match the retrieved source text count as independent evidence.
- Source verification confirms that a cited work exists and matches its metadata; it does not confirm that every detail of the work is correct.
- Numerical agreement of formulas at random points is evidence, not proof.
- Figure correctness is reviewed by the host model unless the figure is generated from code you can inspect (sources are stored under figures/).
- Language-model review of its own text is never counted as independent verification.
