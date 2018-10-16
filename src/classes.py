"""
.. module:: classes
   :synopsis: This module implements Alignment, Template and Residue classes
"""

class Alignment:
    """
    .. class:: Alignment
      This class groups informations about an alignment.

    Attributes:
        score: Score of the alignment
        query_residues: Query's sequence of residues as list of Residues objects
        template: Instance of a Template object as
                  ``Template(template_name, template_residues)``
    """

    def __init__(self, score, query_residues, template_name, template_residues):
        """The constructor of an instance of the Alignment class."""
        self.score = score
        self.query_residues = query_residues
        self.template = Template(template_name, template_residues)

class Template:
    """
    .. class:: Template
      This class groups informations about a template sequence/structure.

    Attributes:
        name: Name of the template
        residues: Template's sequence of residues as list of Residues objects
        pdb: PDB code of the template
    """

    def __init__(self, name, residues):
        """The constructor of an instance of the Template class."""
        self.name = name
        self.residues = residues
        self.pdb = None

class Residue:
    """
    .. class:: Residue
      This class groups informations about a residue.

    Attributes:
        name: Name of the residue (1 letter code)
        ca_coords: 3D coordinates of the residue
    """

    def __init__(self, name):
        """The constructor of an instance of the Residue class."""
        self.name = name
        self.ca_coords = None

    def __str__(self):
        if self.ca_coords == None:
            return "<" + self.name + "  |  " + "empty coordinates>"
        coords = [str(coord) for coord in self.ca_coords]
        return "<" + self.name + "  |  " + str(coords) + ">"
