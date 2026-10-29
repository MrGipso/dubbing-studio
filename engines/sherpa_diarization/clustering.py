"""Grouping speaker turns by voice. Numpy only, so it is testable without the engine environment."""

import numpy as np


def normalized(matrix):
    matrix = np.asarray(matrix, dtype=np.float64)
    return matrix / np.maximum(np.linalg.norm(matrix, axis=-1, keepdims=True), 1e-9)


def cluster(embeddings, weights, num_speakers=None, threshold=0.6):
    """Agglomerative clustering of voice prints (centroid linkage, cosine similarity).

    num_speakers merges down to that many voices; otherwise clusters merge
    while their centroids are more similar than `threshold`. Labels are
    numbered in order of first appearance.
    """
    count = len(embeddings)
    if count == 0:
        return []
    sums = normalized(embeddings) * np.asarray(weights, dtype=np.float64)[:, None]
    labels = list(range(count))
    alive = np.ones(count, dtype=bool)
    centroids = normalized(sums)
    similarity = centroids @ centroids.T
    np.fill_diagonal(similarity, -np.inf)
    target = max(1, int(num_speakers)) if num_speakers else 1
    while alive.sum() > target:
        masked = np.where(alive[:, None] & alive[None, :], similarity, -np.inf)
        i, j = np.unravel_index(np.argmax(masked), masked.shape)
        if not num_speakers and masked[i, j] < threshold:
            break
        sums[i] += sums[j]
        alive[j] = False
        labels = [i if label == j else label for label in labels]
        centroids[i] = normalized(sums[i])
        similarity[i] = similarity[:, i] = centroids @ centroids[i]
        similarity[i, i] = -np.inf
    order = {label: index for index, label in enumerate(dict.fromkeys(labels))}
    return [order[label] for label in labels]


def group_turns(prints, durations, num_speakers=None, threshold=0.6, reliable_seconds=1.0):
    """A speaker label per turn from voice prints of whole turns.

    Prints of very short turns are unreliable: the longer turns are clustered,
    then each short turn gets the closest voice. When the number of speakers
    is found automatically, a "voice" with almost no speech (a cough, a laugh)
    joins the most similar real one.
    """
    prints = np.asarray(prints, dtype=np.float64)
    durations = np.asarray(durations, dtype=np.float64)
    if not len(prints):
        return []
    reliable = durations >= reliable_seconds
    if reliable.sum() < 2:
        reliable[:] = True
    labels = np.full(len(prints), -1)
    labels[reliable] = cluster(prints[reliable], durations[reliable], num_speakers, threshold)
    voices = int(labels.max()) + 1
    centroids = normalized(np.stack([(normalized(prints[labels == k]) * durations[labels == k, None]).sum(0)
                                     for k in range(voices)]))
    for index in np.where(~reliable)[0]:
        labels[index] = int(np.argmax(centroids @ normalized(prints[index])))

    if not num_speakers and voices > 1:
        floor = max(2.0, 0.03 * durations.sum())
        real = [k for k in range(voices) if durations[labels == k].sum() >= floor]
        for k in range(voices):
            if k not in real and real and (labels == k).any():
                labels[labels == k] = max(real, key=lambda m: float(centroids[m] @ centroids[k]))
    order = {label: index for index, label in enumerate(dict.fromkeys(int(value) for value in labels))}
    return [order[int(label)] for label in labels]
