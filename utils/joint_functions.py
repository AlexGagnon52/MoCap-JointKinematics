import ezc3d
import matplotlib.pyplot as plt
import numpy as np


def unit(v: np.ndarray) -> np.ndarray:
    """Scale vectors (last axis) to a length of 1."""
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def dot(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Dot product of two sets of vectors, frame by frame."""
    return np.sum(a * b, axis=-1)


def perpendicular(v: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """Part of v that is at 90 degrees to the unit vector `axis`."""
    return v - dot(v, axis)[..., None] * axis


def rotation_matrix(axis: str, angle: float) -> np.ndarray:
    """Matrix for a turn of `angle` degrees about the X, Y or Z axis."""
    c, s = np.cos(np.radians(angle)), np.sin(np.radians(angle))
    about_x = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    # shifting the rows and columns of Rx by 1 gives Ry, and by 2 gives Rz
    return np.roll(about_x, 'XYZ'.index(axis), axis=(0, 1))


def cardan_matrix(angles: np.ndarray, sequence: str) -> np.ndarray:
    """Rotation matrices built from three angles in a given sequence.

    The first turn is about the first axis of the sequence, the second
    about the new second axis and the third about the new third axis.
    In matrix form this is R1 @ R2 @ R3.

    Parameters
    ----------
    angles : np.ndarray
        Shape (n, 3) or (3,), in degrees: first, second, third angle.
    sequence : str
        Three different axes, for example 'XYZ' or 'ZXY'.

    Returns
    -------
    np.ndarray
        Shape (n, 3, 3), one matrix per row of `angles`.
    """
    first, second, third = [
        np.array([rotation_matrix(axis, angle) for angle in column])
        for axis, column in zip(sequence, np.atleast_2d(angles).T)]
    return first @ second @ third


def cardan_angles(matrices: np.ndarray, sequence: str) -> np.ndarray:
    """Three angles that give back the rotation matrices.

    This undoes `cardan_matrix`. The second angle comes from arcsin, so
    it is limited to -90 to +90 degrees. At exactly +/-90 degrees the
    other two angles cannot be separated (gimbal lock).

    Parameters
    ----------
    matrices : np.ndarray
        Shape (n, 3, 3), rotation matrices.
    sequence : str
        Three different axes, for example 'XYZ' or 'ZXY'.

    Returns
    -------
    np.ndarray
        Shape (n, 3), in degrees: first, second, third angle.
    """
    i, j, k = ['XYZ'.index(axis) for axis in sequence]
    sign = 1 if sequence in 'XYZXY' else -1     # +1 for XYZ, YZX, ZXY
    second = np.arcsin(np.clip(sign * matrices[..., i, k], -1, 1))
    first = np.arctan2(-sign * matrices[..., j, k], matrices[..., k, k])
    third = np.arctan2(-sign * matrices[..., i, j], matrices[..., i, i])
    return np.degrees(np.stack([first, second, third], axis=-1))


def grood_suntay(prox: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """Joint angles with the Joint Coordinate System (right side).

    Uses the equations of Grood & Suntay (1983). e1 is the x axis of
    the proximal segment, e3 is the z axis of the distal segment and
    the floating axis e2 is at 90 degrees to both. Adduction is 90
    degrees minus the angle between e1 and e3.

    Parameters
    ----------
    prox, dist : np.ndarray
        Shape (n, 3, 3), axes of the proximal and distal segments.
        Columns are x (right), y (forward) and z (up the limb).

    Returns
    -------
    np.ndarray
        Shape (n, 3), in degrees: flexion, adduction and external
        rotation.
    """
    prox_x, prox_y, prox_z = np.moveaxis(prox, -1, 0)
    dist_x, dist_y, dist_z = np.moveaxis(dist, -1, 0)
    e2 = unit(np.cross(dist_z, prox_x))                 # floating axis
    flexion = np.arctan2(-dot(e2, prox_z), dot(e2, prox_y))
    adduction = np.arcsin(np.clip(dot(prox_x, dist_z), -1, 1))
    rotation = np.arctan2(-dot(e2, dist_x), dot(e2, dist_y))
    return np.degrees(np.stack([flexion, adduction, rotation], axis=-1))


def load_trial(path: str) -> tuple:
    """Markers (dict of (n, 3) arrays, mm) and time (s) of a 100 Hz c3d."""
    c3d = ezc3d.c3d(path)
    names = c3d['parameters']['POINT']['LABELS']['value']
    points = c3d['data']['points']          # points[axis, marker, frame]
    markers = {name: points[:3, i].T for i, name in enumerate(names)}
    return markers, np.arange(points.shape[2]) / 100


def hip_centre(rasi: np.ndarray, lasi: np.ndarray, rpsi: np.ndarray,
               lpsi: np.ndarray) -> np.ndarray:
    """Right hip joint centre estimated from the four pelvis markers.

    The centre is placed from the middle of the two ASIS markers, using
    fixed fractions (Bell et al., 1990) of the distance between them:
    36 % to the right, 19 % backward and 30 % downward.

    Parameters
    ----------
    rasi, lasi, rpsi, lpsi : np.ndarray
        Shape (n, 3), pelvis markers in mm.

    Returns
    -------
    np.ndarray
        Shape (n, 3), hip centre in mm.
    """
    right = unit(rasi - lasi)
    middle = (rasi + lasi) / 2
    forward = unit(perpendicular(middle - (rpsi + lpsi) / 2, right))
    width = np.linalg.norm(rasi - lasi, axis=1).mean()
    return middle + width * (0.36 * right - 0.19 * forward
                             - 0.30 * np.cross(right, forward))


def limb_frame(top: np.ndarray, marker: np.ndarray, wand: np.ndarray,
               inward: float) -> tuple:
    """Axes of a thigh or a shank and the joint centre at its lower end.

    The joint centre is the side marker moved inward. z points up the
    limb, from that centre to `top`. x points to the right: it is the
    direction in which the wand marker sticks out. y points forward.

    Parameters
    ----------
    top : np.ndarray
        Shape (n, 3), joint centre at the upper end (hip or knee).
    marker : np.ndarray
        Shape (n, 3), side marker at the lower end (knee or ankle).
    wand : np.ndarray
        Shape (n, 3), wand marker of the segment.
    inward : float
        Distance from the side marker to the joint centre, in mm
        (half the joint width plus half the marker diameter).

    Returns
    -------
    tuple
        axes (n, 3, 3) with columns x, y, z, and centre (n, 3).
    """
    rough_z = unit(top - marker)
    side = unit(perpendicular(wand - marker, rough_z))
    centre = marker - inward * side
    z = unit(top - centre)
    x = unit(perpendicular(wand - centre, z))
    return np.stack([x, np.cross(z, x), z], axis=-1), centre


def foot_frame(heel: np.ndarray, toe: np.ndarray,
               p5m: np.ndarray) -> np.ndarray:
    """Right foot axes: y heel to toe, z up from the sole, x to the right."""
    y = unit(toe - heel)
    z = unit(np.cross(p5m - heel, y))
    return np.stack([np.cross(y, z), y, z], axis=-1)


def find_stance(heel: np.ndarray, toe: np.ndarray) -> np.ndarray:
    """True when the heel or the toe moves less than 5 mm per frame."""
    steps = [np.linalg.norm(np.gradient(marker, axis=0), axis=1)
             for marker in (heel, toe)]
    return np.minimum(*steps) < 5


def plot_curves(x: np.ndarray, curves: np.ndarray, labels: list,
                title: str, save_as: str, stance: np.ndarray | None = None,
                xlabel: str = 'Time (s)') -> None:
    """Stacked plots of angles in degrees, one plot per column.

    Parameters
    ----------
    x : np.ndarray
        Shape (n,), values of the horizontal axis.
    curves : np.ndarray
        Shape (n, number of plots), one column per plot, in degrees.
    labels : list
        Name of each column, without the unit.
    title : str
        Title of the figure.
    save_as : str
        Path of the image file to save.
    stance : np.ndarray, optional
        Shape (n,), True when the foot is on the ground (grey bands).
    xlabel : str, optional
        Label of the horizontal axis.
    """
    fig, axes = plt.subplots(len(labels), 1, sharex=True, layout='constrained',
                             figsize=(8, 1 + 2 * len(labels)))
    for ax, curve, label in zip(axes, curves.T, labels):
        ax.plot(x, curve)
        ax.set_ylabel(label + ' (°)')
        ax.grid(True)
        if stance is not None:
            ax.fill_between(x, 0, 1, where=stance, color='grey', alpha=0.15,
                            transform=ax.get_xaxis_transform())
    axes[0].set_title(title)
    axes[-1].set_xlabel(xlabel)
    fig.savefig(save_as, dpi=150)
