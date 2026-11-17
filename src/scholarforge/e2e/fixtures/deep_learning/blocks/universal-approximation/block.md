How expressive are the networks of @def-mlp? A classical answer concerns networks with a single hidden layer.

::: {#thm-universal-approximation name="Universal approximation"}
Let $\phi\colon\mathbb{R}\to\mathbb{R}$ be continuous and sigmoidal, that is, $\phi(t)\to 1$ as $t\to\infty$ and $\phi(t)\to 0$ as $t\to-\infty$. For every continuous $f\colon[0,1]^n\to\mathbb{R}$ and every $\varepsilon>0$ there exist $N\in\mathbb{N}$, coefficients $\alpha_j, b_j\in\mathbb{R}$ and vectors $\mathbf{w}_j\in\mathbb{R}^n$ such that
$$
\sup_{\mathbf{x}\in[0,1]^n}\Big|\,f(\mathbf{x}) - \sum_{j=1}^{N}\alpha_j\,\phi\big(\mathbf{w}_j^\top\mathbf{x} + b_j\big)\Big| < \varepsilon .
$$ {#eq-universal}
:::

This is Cybenko's theorem [@cybenko1989]. @hornik1991 showed that density also holds for every bounded and nonconstant activation, with respect to $L^p(\mu)$ criteria for finite input measures $\mu$, so the essential ingredient is the multilayer architecture rather than the particular activation. The logistic sigmoid of @exm-sigmoid-derivative is sigmoidal in the above sense.

The theorem is an existence statement. It gives no bound on the width $N$ needed for a prescribed $\varepsilon$, it does not say whether gradient-based training as in @thm-gd-convex finds suitable weights, and it says nothing about generalisation from finite data. Quantitative versions require smoothness assumptions on $f$, and a satisfactory explanation of why heavily overparameterised networks trained by stochastic gradient descent generalise well is still an open research question [@lecun2015].

::: {#exm-relu-hat name="Piecewise-linear bumps"}
With the rectified linear unit $\operatorname{ReLU}(t) = \max(0,t)$, the combination $h(t) = \operatorname{ReLU}(t) - 2\operatorname{ReLU}(t - \tfrac12) + \operatorname{ReLU}(t-1)$ is a hat function that vanishes outside $[0,1]$ and peaks at $h(\tfrac12) = \tfrac12$. Sums of shifted and scaled hats reproduce piecewise-linear interpolation, which converges uniformly to any continuous function on an interval as the mesh is refined: a constructive glimpse of @thm-universal-approximation in one dimension, with ReLU in place of a sigmoidal function.
:::

::: {#exr-hat}
Show that a suitable sum of $m+1$ hats with nodes $0, 1/m, \dots, 1$ interpolates $f$ at the nodes, and bound the uniform error when $f$ is Lipschitz with constant $K$.
:::
