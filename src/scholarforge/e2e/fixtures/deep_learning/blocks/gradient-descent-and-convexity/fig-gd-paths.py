import numpy as np
import matplotlib.pyplot as plt

h = np.array([1.0, 10.0])


def path(eta, steps=25):
    x = np.array([-9.0, 1.5])
    pts = [x.copy()]
    for _ in range(steps):
        x = x - eta * h * x
        pts.append(x.copy())
    return np.array(pts)


g1, g2 = np.meshgrid(np.linspace(-10, 2, 300), np.linspace(-2.2, 2.2, 300))
f = 0.5 * (h[0] * g1 ** 2 + h[1] * g2 ** 2)
fig, ax = plt.subplots(figsize=(6.2, 3.3))
ax.contour(g1, g2, f, levels=[0.5, 2, 5, 10, 20, 35, 50], colors="0.65", linewidths=0.7)
for eta, style, label in ((0.1, "-", r"$\eta = 0.1$"), (0.19, "--", r"$\eta = 0.19$")):
    p = path(eta)
    ax.plot(p[:, 0], p[:, 1], linestyle=style, marker="o", markersize=2.5, color="black", linewidth=1.0,
            label=label)
ax.plot([0], [0], marker="*", markersize=9, color="black")
ax.set_xlabel(r"$x_1$")
ax.set_ylabel(r"$x_2$")
ax.set_aspect("auto")
ax.legend(loc="upper left", frameon=False)
