import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Circle

nodes = {
    "v1": (0.0, 1.0, r"$v_1 = x_1$"),
    "v2": (0.0, -1.0, r"$v_2 = x_2$"),
    "v3": (2.2, -0.5, r"$v_3 = v_1 v_2$"),
    "v4": (2.2, 1.3, r"$v_4 = \sin v_1$"),
    "v5": (4.4, 0.4, r"$v_5 = v_3 + v_4$"),
}
edges = [("v1", "v3"), ("v2", "v3"), ("v1", "v4"), ("v3", "v5"), ("v4", "v5")]
fig, ax = plt.subplots(figsize=(6.0, 2.8))
r = 0.36
for key, (x, y, label) in nodes.items():
    ax.add_patch(Circle((x, y), r, facecolor="white", edgecolor="black", linewidth=1.2, zorder=3))
    ax.text(x, y, key.replace("v", r"$v_") + "$", ha="center", va="center", fontsize=10, zorder=4)
    ax.text(x, y - 0.62, label, ha="center", va="center", fontsize=8.5)
for a, b in edges:
    xa, ya, _ = nodes[a]
    xb, yb, _ = nodes[b]
    ax.add_patch(FancyArrowPatch((xa, ya), (xb, yb), arrowstyle="-|>", mutation_scale=12, color="black",
                                 shrinkA=r * 72 / 1.6, shrinkB=r * 72 / 1.6, linewidth=1.0))
ax.set_xlim(-0.8, 5.3)
ax.set_ylim(-1.9, 2.0)
ax.set_aspect("equal")
ax.axis("off")
