"""
.. module:: Alignment
   :synopsis: This module implements the Alignment class.
"""

# Third-party modules
import contextlib
import os
import re
import pathlib
import logging
import numpy as np
from Bio.SubsMat import MatrixInfo
from Bio.SeqUtils import seq3
from Bio.PDB import PDBParser
from Bio.PDB import DSSP
import modeller as m
import modeller.automodel as am
from modeller.automodel import assess

logging.basicConfig(filename="run_warnings.log", level=logging.WARNING)
logging.captureWarnings(True)


def process(dist_range, dope_dict, output_path, index_list, top_couplings_dict, ali):
    """
        Generates the threading, modeller, secondary structure, solvent accessibility and
        co-evolution scores for a given Alignment.

        Args:
            dist_range (int list): List of two values representing the range of distances between
                                   two CA atoms to take into account for the threading algorithm.
            dope_dict (dict): Dictionnary structure storing the DOPE energy values according to
                              residues distances.
            output_path (str): The path to the results directory.
            index_list (list): A list of gapless position indexes
            top_couplings_dict (dict): A dictionary with key = ranking of ccmpred top couple based
                                       on ss_confidence and value = index aa1, index aa2
            ali (Alignment object): An alignment object to process.

        Returns:
            tuple: (Sum of the different scores, Number of the Alignment,
            Template's name, Template's benchmark).

    """
    # Calculate the threading score of all alignments and find the initial templates
    threading_score = ali.calculate_threading_score(dist_range, dope_dict)
    # Calculate the modeller score of all alignments
    modeller_score = ali.calculate_modeller_score(output_path)
    # Calculate secondary structure score
    ss_score = ali.calculate_ss_score()
    # Calculate solvent accessibility score
    solvent_access_score = ali.calculate_solvent_access_score(0.25)
    # Calculate co-evolution score
    ccmpred_score = ali.calculate_coevolution_score(index_list, top_couplings_dict)
    return ali.num, ali.score, threading_score, modeller_score, ss_score, solvent_access_score,\
        ccmpred_score, ali.template.name, ali.template.benchmark


def clean_modeller_outputs(modeller_out_dir):
    """
    Removes all the useless files generated by MODELLER.

    Args:
        dir (str): Path to directory to clean.
        pattern (str): Regex expression for files to remove.
    """
    pattern = "^[^\\.|alignments].*$"
    for file in os.listdir(modeller_out_dir):
        if re.search(pattern, file):
            os.remove(os.path.join(modeller_out_dir, file))

def keep_accessible_residues(dssp_rsa, threshold):
    """
     From the output of DSSP we keep only accessible residues which have an RSA
     value > threshold (arbitrary threshold).

     Args:
         dssp_rsa (dict): A dictionary as keys = residue index and value = RSA.
         threshold (int): Relative solvant accessibility threshold.

     Returns:
         dict(dict): Keys are the residue ids and as value their solvant accessible area.
    """
    return {key: val for key, val in dssp_rsa.items() if val > threshold}


class Alignment:
    """
    .. class:: Alignment

      This class groups informations about an alignment.

    Attributes:
        num (int): Number of the alignment.
        score (float): Score of the alignment.
        query (Query object): Instance of a Query object as
                              ``Query(query_residues, query_first, query_last)``.
        template (Template object): Instance of a Template object as
                                    ``Template(template_name, template_residues)``.
    """

    def __init__(self, num, score, query, template):
        self.num = num
        self.score = score
        self.query = query
        self.template = template

    def calculate_threading_score(self, dist_range, dope_dict):
        """
            Calculate the threading score of the query on the template sequence.
            1) For each pair residues of the query sequence the distance between them is
            calculated using the coordinates of the template sequence.The distance calculation
            is optimized.
            2) The calculated distance is then convert into a dope energy value.
            3) All elements of the energy matrix generated are finally sum and returned as the
            opposite (multiplied by -1) since it is an energy score. This is to simplify the
            min/max normalization afterwards.

            Args:
                dist_range (list of int): Range of distances in angstroms. Distances
                                          within this range only are taken into account.
                dope_dict (dict): A dictionary with key = res_1-res_2 and
                                        value = an array of 30 dope energy values.

            Returns:
                sum of energy matrix(float): Threading score calculated.
        """
        gap_penalty = 0
        query = self.query.residues
        template = self.template.residues

        query_size = self.query.get_size()
        # This sets a numpy matrix of shape query * query which will contain all
        # the energies corresponding to distances between all pairs of residues
        # between the query and itself: the coordinates are the ones from the template.
        energy = np.empty((query_size, query_size), dtype=object)
        # Filling the matrix afterwards with "NaN" is faster
        energy.fill(np.nan)
        for i, row_res in enumerate(query):
            # The gap was already treated
            if i <= (query_size - 3) and energy[i, (i + 2)] == gap_penalty:
                continue
            # There is a gap in the query or the template
            elif row_res.name == "-" or template[i].name == "-":
                # The whole line is set with gap penalty value
                energy[i, (i + 2):] = gap_penalty
                continue
            for j in range(i + 2, query_size - 1):
                col_res = query[j]
                # The gap was already treated
                if energy[i, j] == gap_penalty:
                    continue
                # There is a gap in the query or the template
                elif col_res.name == "-" or template[j].name == "-":
                    # The whole column is set with gap penalty value
                    energy[:(j - 1), j] = gap_penalty
                    continue
                else:
                    # Calculate to distance between two residues
                    dist = template[i].calculate_distance(template[j], "CA")
                    # Keep distances only in a defined range because we don't want to
                    # take into account directly bonded residues (dist < ~5 A) and too far residues
                    if dist_range[0] <= dist <= dist_range[1]:
                        # DOPE energy values spread between 0.25 and 15 by 0.5 intervals
                        # So 30 intervals and max value = 15
                        interval_index = round(int((dist * 30) / 15))
                        energy[i, j] = dope_dict[row_res.name + col_res.name][interval_index]
        # Return the sum of energy matrix with numpy's "Nan" interpreted as zeros
        return np.nansum(energy) * (-1)

    def calculate_ss_score(self):
        """
            This function calculates a score based on the secondary structure prediction of the
            query. We consider only PSIPRED predictions with a confidence >= 7 on the scale 0-9.

            The reason is that we want secondary structure predictions that have at least a Q3
            accuracy of 80% (cf. doi:[10.1186/1471-2164-11-S4-S4]).
            We use the average three-state prediction accuracy (Q3) to measure the accuracy of the
            secondary structure prediction of PSIPRED.
            The formula is:

            .. math::

               score = {N - total\_incorrect \over N}

            With N = length of the gapless query and total_incorrect = all the incorrectly predicted
            secondary structures with a confidence score < 7 (True negatives).
            The template used is the one generated by Modeller.

            Returns:
                secondary structure score(float): the proportion of well predicted secondary
                structure predictions.
        """
        total_incorrect = 0
        score = 0
        query_ind = 0
        templ_ind = 0
        ind = 0
        query_len = self.query.get_size()
        while ind < query_len:
            # Skip gaps in secondary structure predictions
            while query_ind < query_len and self.query.residues[query_ind].secondary_struct == "-":
                query_ind += 1
            while (templ_ind < query_len\
                    and self.template.residues[templ_ind].secondary_struct == "-"):
                templ_ind += 1
            # Count incorrect secondary structure predictions of query according to the template
            if (query_ind < query_len and templ_ind < query_len\
                and self.query.residues[query_ind].ss_confidence < 7\
                    and self.query.residues[query_ind].secondary_struct\
                    != self.template.residues[templ_ind].secondary_struct):
                total_incorrect += 1
            ind += 1
            query_ind += 1
            templ_ind += 1
        try:
            # Calculate Q3
            gapless_query_len = len([res for res in self.query.residues if res.name != "-"])
            score = (gapless_query_len - total_incorrect) / gapless_query_len
        except ZeroDivisionError as err:
            print(str(err), "\n\nError ss_score: the query seems to be of size null")
        return score

    def calculate_distance_matrix(self, size):
        """
            Calculate the matrix of distances between the all residues (beta-carbon or alpha-carbon
            otherwise) of the query sequence. Using the coordinates of the template sequence.
            The distance calculation is optimized.

            Args:
                size (int): Real size of the query sequence.

            Returns:
                2D numpy matrix: The distance matrix between pairs of residue of the query sequence.
        """
        query_size = self.query.get_size()
        distance = np.empty((size, size), dtype=object)
        # We fill the matrix with "x" when the alignment between the query and the template is not
        # starting by the first residue of the query sequence
        pos = 0
        while pos < self.query.first-1:
            distance[pos, (pos+1):] = "x"
            pos += 1

        index_i = 0
        for dist_i in range(pos, self.query.last):
            # Until there is a gap in the aligned query, we pass to the next index
            while index_i < query_size and self.query.residues[index_i].name == "-":
                index_i += 1
            # If there is a gap in the aligned template, we put an "x" and pass to the next index
            if index_i < query_size and self.template.residues[index_i].name == "-":
                distance[dist_i, (dist_i+1):] = "x"
                index_i += 1
                continue
            index_j = index_i + 1
            for dist_j in range(dist_i+1, self.query.last):
                # Until there is a gap in the aligned query, we pass to the next index
                while index_j < query_size and self.query.residues[index_j].name == "-":
                    index_j += 1
                #If there is a gap in the aligned template, we put an "x" and pass to the next index
                if index_j < query_size and self.template.residues[index_j].name == "-":
                    distance[dist_j, (dist_j+1):] = "x"
                    index_j += 1
                    continue
                if distance[dist_i, dist_j] != "x":
                    # Distance between beta-carbon (alpha-carbon otherwise) is calculated
                    if self.template.residues[index_i].cb_atom.coords != None\
                            and self.template.residues[index_j].cb_atom.coords != None:
                        distance[dist_i, dist_j] = self.template.residues[index_i]\
                            .calculate_distance(self.template.residues[index_j], "CB")
                    else:
                        distance[dist_i, dist_j] = self.template.residues[index_i]\
                            .calculate_distance(self.template.residues[index_j], "CA")
                index_j += 1
            index_i += 1
        pos = dist_i
        # As previously, we fill the matrix with "x" when the alignment between the query and
        # the template is not ending by the last residue of the query sequence
        while pos < size-1:
            distance[:pos, (pos+1):] = "x"
            pos += 1
        return distance

    def calculate_coevolution_score(self, index_list, top_couplings_dict):
        """
            Compare top contacts calculated with ccmpred in the query with corresponding calculated
            distances.

            Args:
                top_couplings_dict(dict): top ranking couplings indexes in the query
                distance_matrix(float): distance matrix of the query against itself
            Returns:
                contact_score(float):log10(1+ number of true contacts between ccmpred/distance
                matrix).
        """
        distance_matrix = self.calculate_distance_matrix(len(index_list))
        true_pos = 0
        for top_position in top_couplings_dict.values():
            #as matrix is triange, get matrix [i,j]
            dist = distance_matrix[top_position[0], top_position[1]]
            dist_inv = distance_matrix[top_position[1], top_position[0]]
            if (isinstance(dist, float) and dist < 8)\
            or (isinstance(dist_inv, float) and dist_inv < 8):
                true_pos += 1
        
        # Spread of the values
        contact_score = np.log10(1+true_pos*(self.query.last - self.query.first))
        return contact_score

    def calculate_solvent_access_score(self, threshold):
         '''
             Calculate the accessibility score between the predicted model
             and the template pdb structure.
             Args:
                 dssp_bin (String): Path to the dssp binary.
                 threshold cutoff (int): Cutoff for relative accessibility residue values.
             Returns:
                 float: The Accessibility score calculated
         '''
         # Path to the PDBs of predicted and template models
         pred_model_pdb = "data/pdb/"+self.template.name+"/"+self.template.modeller_pdb+".atm"
         template_pdb = "data/pdb/"+self.template.name+"/"+self.template.reindexed_pdb+".atm"
         # Parse PDBs
         pred_model = PDBParser(QUIET=True).get_structure("pred_model", pred_model_pdb)[0]
         template_model = PDBParser(QUIET=True).get_structure("template_model", template_pdb)[0]
         # Run DSSP on both PDB files of the template and the Modeller's model
         dssp_pred_model = DSSP(pred_model, pred_model_pdb, dssp="bin/dssp-2.0.4-linux-amd64")
         dssp_template_model = DSSP(template_model, template_pdb, dssp="bin/dssp-2.0.4-linux-amd64")
         # Parse the DSSP output to retrieve the relative % of solvant accessible area for each CA.
         #get alignement index
         query_index_ali = [index for index, residue in enumerate(self.query.residues) if str(residue) != "-"]
         template_index_ali = [index for index, residue in enumerate(self.template.residues) if str(residue) != "-"]
         #attribuate alignemnt index
         rsa_pred_model = dict(zip(query_index_ali, [dssp_pred_model[key][3]
             for key in dssp_pred_model.keys()]))
         rsa_template_model = dict(zip(template_index_ali, [dssp_template_model[key][3]
            for key in dssp_template_model.keys()]))
         # Keep only residues under a relative accessibilities threshold: buried residues
         pred_access_residues = keep_accessible_residues(rsa_pred_model, threshold)
         template_access_residues = keep_accessible_residues(rsa_template_model, threshold)

         # Get the common buried residues
         common_residues_len = len(set(pred_access_residues).intersection(template_access_residues))
         # Normalization
         return common_residues_len/len(query_index_ali)

    def write_alignment_for_modeller(self, ali_path):
        """
            Modeller requires a specifically formatted alignment file ".ali",
            following the PIR type format.
            This function writes this .ali file for the current alignment between
            the query and the template.

            Example of an alignment.ali file for Modeller::

                >P1;5fd1
                structureX:5fd1:1:A:106:A:ferredoxin:Azotobacter vinelandii:1.90: 0.19
                AFVVTDNCIKCKYTDCVEVCPVDCFYEGPNFLVIHPDECIDCALCEPECPAQAIFSEDEVPEDMQEFIQLNAELA
                EVWPNITEKKDPLPDAEDWDGVKGKLQHLER*

                >P1;1fdx
                sequence:1fdx:1::54::ferredoxin:Peptococcus aerogenes:2.00:-1.00
                AYVINDSC--IACGACKPECPVNIIQGS--IYAIDADSCIDCGSCASVCPVGAPNPED-----------------
                -------------------------------*

        """
        with open(ali_path + self.template.name+".ali", "w") as ali_out:
            template_len = len([res for res in self.template.residues if res.name != '-'])
            ali_out.write(">P1;" + self.template.reindexed_pdb)
            ali_out.write("\nstructure:" + self.template.reindexed_pdb
                          + ":" + str(self.template.first) + ":@:"
                          + str(template_len)
                          + ":@::::\n")
            ali_out.write(self.template.display() + "*")
            ali_out.write("\n>P1;query_" + self.template.name)
            ali_out.write("\nsequence:::::::::\n")
            ali_out.write(self.query.display(modeller=True) + "*")

    def calculate_modeller_score(self, res_path):
        """
            * This function constructs a single comparative model for the query
              sequence from the known template structure, using alignment.ali,
              a PIR format alignment of query and template. The final model is
              written into the PDB file.
            * This function also returns the DOPE assessed score of the model
              generated by MODELLER. It actually returns the opposite (multiplied by -1) since it
              is an energy score. This is to simplify the min/max normalization done afterwards.
              DOPE is the most reliable score at separating native-like models from decoys (lower,
              i.e, more negative, DOPE scores tend to correlate with more native-like models).

            Args:
                res_path (str): Path to the results folder.

            Returns:
                score(float): The DOPE score (multiplied by -1) of the model generated by MODELLER.
        """
        root_dir = os.getcwd()
        modeller_out_dir = res_path + "/modeller/"
        ali_dir = "alignments/"
        pathlib.Path(modeller_out_dir + ali_dir).mkdir(parents=True, exist_ok=True)
        # MODELLER generates the result files in his current directory, so we must
        # go to the results directory and come back to root dir afterwards.
        os.chdir(modeller_out_dir)
        path_to_atm = root_dir + "/data/pdb/" + self.template.name
        # We reindex all the PDB files to avoid any problem with modeller
        self.template.reindex_pdb(1, path_to_atm, True)
        # Parse the new PDB to get new residues and their coordinates generated by MODELLER
        self.template.parse_pdb(path_to_atm + "/" + self.template.reindexed_pdb + ".atm")
        # Write Modeller's alignment PIR file
        self.write_alignment_for_modeller("./alignments/")
        # Redirect Modeller's verbose into nothingness, nil, chaos and abysses !
        with contextlib.redirect_stdout(None):
            # create a new MODELLER environment to build this model in
            m.env = m.environ()
            # directories for input atom files
            m.env.io.atom_files_directory = [path_to_atm]
            a_model = am.automodel(m.env,
                                   # alignment filename
                                   alnfile=ali_dir + self.template.name + '.ali',
                                   # codes of the templates
                                   knowns=self.template.reindexed_pdb,
                                   # code of the target
                                   sequence='query_' + self.template.name,
                                   # DOPEHR is very similar to DOPEHR but is obtained at
                                   # Higher Resolution (using a bin size of 0.125Å
                                   # rather than 0.5Å).
                                   assess_methods=assess.DOPEHR)
            a_model.very_fast()
            # index of the first and last model (determines how many models to calculate)
            a_model.starting_model = 1
            a_model.ending_model = 1
            modeller_dope_score = 0
            # Catch any errors that Modeller can raise and write them in the log file
            try:
                a_model.make()
            except m.ModellerError as err:
                logging.warning("Modeller error with " + self.template.name + " | "
                                + self.template.pdb, str(err))
        new_model_pdb = a_model.outputs[0]["name"]
        modeller_dope_score = a_model.outputs[0]["DOPE-HR score"]
        self.template.modeller_pdb = self.template.pdb + "_mod"
        # Move the new model to the PDB directory and rename it
        os.rename(new_model_pdb, path_to_atm + "/" + self.template.modeller_pdb + ".atm")
        # Parse the new model generated by MODELLER to get the residues and their coordinates
        self.template.parse_pdb(path_to_atm + "/" + self.template.modeller_pdb + ".atm")
        # Go back to root directory
        os.chdir(root_dir)
        return modeller_dope_score * (-1)

    def calculate_blosum_score(self):
        """
            Calculate a blosum score using the blosum62 matrix. This matrix contains
            substitution scores for each amino acid pair. A positive score is given to the more
            likely substitutions while a negative score is given to the less likely substitutions.

            Returns:
                int: The blosum score calculated.
        """
        # dictionary containing substitution scores of the blosum62 matrix
        blosum62 = MatrixInfo.blosum62
        score = 0
        for ind, res_q in enumerate(self.query.residues):
            res_t = self.template.residues[ind]
            if res_q.name == "-" or res_t.name == "-":
                continue
            # The blosum62 matrix is symetric, thus we need to test
            # if (res_1, res_2) key is in the dictionary.
            if (res_q.name, res_t.name) in blosum62:
                score += blosum62[(res_q.name, res_t.name)]
            # If not, the corresponding key is (res_2, res_1)
            else:
                score += blosum62[(res_t.name, res_q.name)]
        return score

    def write_pdb(self, pdb_path):
        """
            Write a pdb file by threading the query sequence on the template CA coordinates.

            Args:
                pdb_path (str): Path of the pdb file to create.
        """
        with open(pdb_path, "w") as file:
            # Extra informations on the template used to generate the pdb file
            file.write("REMARK Threading of query sequence on the {:s} template #{:d}.\n"
                       .format(self.template.name, self.num))
            ind = 0
            count_atom = 1
            for count_res in range(self.query.first, self.query.last+1):
                res_t = self.template.residues[ind]
                res_q = self.query.residues[ind]
                if res_q.name == "-" or res_t.name == "-":
                    ind += 1
                    continue
                # # N "ATOM" line
                file.write("{:6s}{:5d} {:^4s} {:>3s}{:>2s}{:4d}{:>12.3f}{:8.3f}{:8.3f}{:6.2f}{:6.2f}{:>12s}\n"
                           .format("ATOM", count_atom, "N", seq3(res_q.name).upper(), "A",
                                   count_res,
                                   res_t.n_atom.coords[0],
                                   res_t.n_atom.coords[1],
                                   res_t.n_atom.coords[2],
                                   1.00, 0, "N"))
                count_atom += 1
                # CA "ATOM" line
                file.write("{:6s}{:5d} {:^4s} {:>3s}{:>2s}{:4d}{:>12.3f}{:8.3f}{:8.3f}{:6.2f}{:6.2f}{:>12s}\n"
                           .format("ATOM", count_atom, "CA", seq3(res_q.name).upper(), "A",
                                   count_res,
                                   res_t.ca_atom.coords[0],
                                   res_t.ca_atom.coords[1],
                                   res_t.ca_atom.coords[2],
                                   1.00, 0, "C"))
                count_atom += 1
                # C "ATOM" line
                file.write("{:6s}{:5d} {:^4s} {:>3s}{:>2s}{:4d}{:>12.3f}{:8.3f}{:8.3f}{:6.2f}{:6.2f}{:>12s}\n"
                           .format("ATOM", count_atom, "C", seq3(res_q.name).upper(), "A",
                                   count_res,
                                   res_t.c_atom.coords[0],
                                   res_t.c_atom.coords[1],
                                   res_t.c_atom.coords[2],
                                   1.00, 0, "C"))
                count_atom += 1
                ind += 1
            # The two last lines of the created pdb file ("END" and "TER" lines)
            file.write("END\n")
