"""
    .. module:: threading
      :synopsis: This module implements all the functions to make the threading of
                 the query sequence on the templates.
"""


import numpy as np





def calc_dist_matrix(query, template, dist_range):
    """
        Calculate the matrix of distances of pair residues of the query sequence,
        using the coordinates of the template sequence: threading of the query
        on the template sequence. The distance calculation is optimized.

        Args:
            query: List of residues of the query sequence
            template: List of residues of the template sequence
            dist_range: Range of distances in angströms.
                        Distances within this range only are taken into account

        Returns:
            2D numpy matrix: The distance matrix between pairs of residues of the
            query sequence after being threaded on the template sequence.
    """

    query_size = len(query)
    matrix = np.full((query_size, query_size), fill_value=np.inf, dtype=object)
    # The distance matrix is symmetric so we make the calculations only for
    # the upper right triangular matrix. This saves have computation time.
    for i in range(len(query)):
        for j in range(i, len(query)):
            row_res = query[i]
            col_res = query[j]
            # A gap represented by "-" = no distance calculation.
            # The whole line / row in the matrix will necessarily be "*"
            # This saves computation time
            if row_res.name == "-" or template[i].name == "-":
                matrix[i, j:] = "*"
                break
            elif col_res.name == "-" or template[j].name == "-":
                matrix[:i, j] = "*"
                break
            else:
                # One of the most efficient method to calculate the distances
                # https://stackoverflow.com/a/47775357/6401758
                # distance = sqrt((xa-xb)**2 + (ya-yb)**2 + (za-zb)**2)
                dist = np.linalg.norm(template[i].CA_coords - template[j].CA_coords)
                # Keep distances only in a defined range because we don't want to
                # take into account directly bonded residues (dist < ~5 A) and too far residues
                if dist_range[0] <= dist <= dist_range[1]:
                    matrix[i, j] = dist
    return matrix
