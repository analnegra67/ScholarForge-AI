import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(-6, 6, 600)
s = 1.0 / (1.0 + np.exp(-x))
fig, ax = plt.subplots(figsize=(6.0, 3.4))
ax.plot(x, s, color="black", linestyle="-", label=r"$\sigma(x)$")
ax.plot(x, s * (1 - s), color="black", linestyle="--", label=r"$\sigma'(x)$")
ax.axhline(0.25, color="0.6", linewidth=0.8, linestyle=":")
ax.annotate(r"$\sigma'(0) = 1/4$", xy=(0, 0.25), xytext=(1.6, 0.36),
            arrowprops={"arrowstyle": "->", "color": "0.3"})
ax.set_xlabel(r"$x$")
ax.set_ylim(-0.02, 1.05)
ax.legend(loc="center right", frameon=False)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
