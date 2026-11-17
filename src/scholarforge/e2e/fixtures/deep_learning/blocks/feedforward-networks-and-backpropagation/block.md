::: {#def-mlp name="Feedforward network"}
A feedforward network with $L$ layers maps an input $\mathbf{a}^{(0)} = \mathbf{x}\in\mathbb{R}^{n_0}$ to an output $\mathbf{a}^{(L)}$ through
$$
\mathbf{z}^{(\ell)} = \mathbf{W}^{(\ell)}\mathbf{a}^{(\ell-1)} + \mathbf{b}^{(\ell)},\qquad \mathbf{a}^{(\ell)} = \phi\big(\mathbf{z}^{(\ell)}\big),\qquad \ell = 1,\dots,L,
$$ {#eq-mlp}
with weight matrices $\mathbf{W}^{(\ell)}\in\mathbb{R}^{n_\ell\times n_{\ell-1}}$, bias vectors $\mathbf{b}^{(\ell)}\in\mathbb{R}^{n_\ell}$ and an activation function $\phi$ applied entrywise; in the output layer $\phi$ is often replaced by the identity or by softmax.
:::

In this chapter $L$ counts layers; it is unrelated to the smoothness constant of @sec-optimization-gradient-descent-and-convexity. For classification into $K$ classes the last layer produces scores $\mathbf{z}\in\mathbb{R}^K$, which are turned into probabilities by $\operatorname{softmax}(\mathbf{z})_k = e^{z_k}/\sum_{j} e^{z_j}$ and scored with the cross-entropy $\ell(\mathbf{z}, y) = -\log\operatorname{softmax}(\mathbf{z})_y$ of the true class $y$.

::: {#prp-softmax-gradient name="Gradient of the softmax cross-entropy"}
With $\mathbf{p} = \operatorname{softmax}(\mathbf{z})$ and $\mathbf{e}_y$ the $y$-th standard basis vector, $\nabla_{\mathbf{z}}\,\ell = \mathbf{p} - \mathbf{e}_y$.
:::

::: {.proof}
We have $\ell = -z_y + \log\sum_j e^{z_j}$. The derivative of the second term with respect to $z_k$ is $e^{z_k}/\sum_j e^{z_j} = p_k$, and the derivative of $-z_y$ is $-1$ if $k = y$ and $0$ otherwise.
:::

Backpropagation computes all parameter gradients with one reverse sweep, as guaranteed by @prp-reverse-cost. Writing $\boldsymbol{\delta}^{(\ell)} = \partial\ell/\partial\mathbf{z}^{(\ell)}$, the chain rule applied to @eq-mlp gives
$$
\boldsymbol{\delta}^{(\ell)} = \Big(\mathbf{W}^{(\ell+1)\top}\boldsymbol{\delta}^{(\ell+1)}\Big)\odot\phi'\big(\mathbf{z}^{(\ell)}\big),\qquad
\frac{\partial\ell}{\partial\mathbf{W}^{(\ell)}} = \boldsymbol{\delta}^{(\ell)}\,\mathbf{a}^{(\ell-1)\top},\qquad
\frac{\partial\ell}{\partial\mathbf{b}^{(\ell)}} = \boldsymbol{\delta}^{(\ell)},
$$ {#eq-backprop}
where $\odot$ denotes the entrywise product and the recursion starts from $\boldsymbol{\delta}^{(L)} = \mathbf{p} - \mathbf{e}_y$ by @prp-softmax-gradient. The procedure was popularised for training multilayer networks by @rumelhart1986, whose experiments showed that hidden units come to represent important features of the task.

::: {#exm-two-layer name="A two-layer network by hand"}
Take one input $x$, one hidden unit with weight $w_1$ and sigmoid activation, one linear output with weight $w_2$, and the squared loss $\ell = \tfrac12\big(w_2\,\sigma(w_1 x) - t\big)^2$ for a target $t$. Then $\partial\ell/\partial w_2 = \big(w_2\sigma(w_1 x) - t\big)\,\sigma(w_1 x)$ and, using @eq-sigmoid-derivative, $\partial\ell/\partial w_1 = \big(w_2\sigma(w_1x) - t\big)\,w_2\,\sigma(w_1x)\big(1-\sigma(w_1x)\big)\,x$. These are exactly the two steps of @eq-backprop for this tiny network.
:::

::: {#exr-backprop-shapes}
Check that every product in @eq-backprop has compatible dimensions, and rewrite the recursion for the activation $\phi = \tanh$.
:::
