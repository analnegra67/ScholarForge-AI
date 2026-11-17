A program that computes $f\colon\mathbb{R}^n\to\mathbb{R}$ from elementary operations defines a *computational graph*: its nodes are intermediate values $v_i$, and its edges record which values each operation reads. Automatic differentiation (AD) applies @thm-chain-rule to this graph and produces derivatives that are exact up to floating-point rounding. It differs from finite differences, which suffer from truncation and cancellation errors, and from symbolic differentiation, which can produce very large expressions [@baydin2018].

::: {#exm-ad-trace name="Forward and reverse sweeps by hand"}
Consider $f(x_1, x_2) = x_1 x_2 + \sin x_1$, evaluated by the trace
$$
v_1 = x_1,\quad v_2 = x_2,\quad v_3 = v_1 v_2,\quad v_4 = \sin v_1,\quad v_5 = v_3 + v_4 = f ,
$$ {#eq-ad-trace}
whose graph is drawn in @fig-comp-graph.

*Forward mode* propagates tangents $\dot v_i = \partial v_i/\partial x_1$ together with the values: $\dot v_1 = 1$, $\dot v_2 = 0$, $\dot v_3 = \dot v_1 v_2 + v_1\dot v_2$, $\dot v_4 = \cos(v_1)\,\dot v_1$ and $\dot v_5 = \dot v_3 + \dot v_4$, so that $\dot v_5 = x_2 + \cos x_1 = \partial f/\partial x_1$. One forward sweep yields one directional derivative, hence the full gradient of a function of $n$ inputs needs $n$ sweeps.

*Reverse mode* first evaluates @eq-ad-trace and stores the values, then propagates adjoints $\bar v_i = \partial f/\partial v_i$ backwards from $\bar v_5 = 1$:
$$
\bar v_3 = \bar v_5,\quad \bar v_4 = \bar v_5,\quad \bar v_1 = \bar v_3\, v_2 + \bar v_4\cos v_1,\quad \bar v_2 = \bar v_3\, v_1 .
$$ {#eq-ad-adjoints}
A single reverse sweep therefore returns the whole gradient $\nabla f = (\bar v_1, \bar v_2)^\top = (x_2 + \cos x_1,\; x_1)^\top$.
:::

<!-- sf:figure fig-comp-graph -->

::: {#prp-reverse-cost name="Cost of reverse mode"}
For a scalar function evaluated by a program with $N$ elementary operations, reverse-mode AD computes the full gradient with a number of operations bounded by a small constant multiple of $N$, independently of the number $n$ of inputs. The price is memory: the intermediate values of the forward sweep must be stored or recomputed [@griewank2008].
:::

This *cheap gradient principle* is what makes training networks with millions of parameters feasible. Backpropagation, derived in @sec-neural-networks-feedforward-networks-and-backpropagation, is reverse-mode AD applied to the loss of a network; forward mode remains preferable when a function has few inputs and many outputs [@baydin2018].

::: {#exr-ad-trace}
Write the evaluation trace and the reverse sweep for $g(x, y) = e^{xy}/(1 + y^2)$, and check the resulting gradient against direct differentiation.
:::
