###############################################################################
#                                                                             #
#    This program is free software: you can redistribute it and/or modify     #
#    it under the terms of the GNU General Public License as published by     #
#    the Free Software Foundation, either version 3 of the License, or        #
#    (at your option) any later version.                                      #
#                                                                             #
#    This program is distributed in the hope that it will be useful,          #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of           #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the            #
#    GNU General Public License for more details.                             #
#                                                                             #
#    You should have received a copy of the GNU General Public License        #
#    along with this program. If not, see <http://www.gnu.org/licenses/>.     #
#                                                                             #
###############################################################################

import os
import logging
import ntpath
from collections import defaultdict

from genome_tree_tk.defaultValues import DefaultValues
from genome_tree_tk.markers.align_markers import AlignMarkers
from genome_tree_tk.common import read_genome_ids

from biolib.external.fasttree import FastTree

import dendropy


class InferMarkers(object):
    """Identify ubiquitous, single-copy marker gene within a set of genomes.

    Currently, this class is specifically designed to work with reference
    genomes from IMG. In particular, it assumes each genome is contain in
    a separate directory and that Pfam and TIGRFAMs annotations are
    available.
    """

    def __init__(self, genome_quality_file, img_genome_dir, pfam_model_file, tigrfams_model_dir, cpus):
        """Initialization.

        Parameters
        ----------
        genome_quality_file : str
            File specifying completeness and contamination of genomes.
        img_genome_dir : str
            Directory with genomes in individual directories.
        pfam_model_file : str
            File containing Pfam HMMs.
        tigrfams_model_dir : str
            Directory containing TIGRFAMs HMMs.
        cpus : int
            Number of cpus to use.
        """

        self.logger = logging.getLogger()

        self.genome_quality_file = genome_quality_file
        self.img_genome_dir = img_genome_dir
        self.pfam_model_file = pfam_model_file
        self.tigrfams_model_dir = tigrfams_model_dir

        self.cpus = cpus

        self.pfam_extension = '.pfam.tab.txt'
        self.tigr_extension = '.tigrfam.tab.txt'

        self.pfam_annotation_index = 8
        self.pfam_bitscore_index = 7

        self.tigr_annotation_index = 6
        self.tigr_bitscore_index = 5

    def _trusted_genomes(self, genome_ids, trusted_comp, trusted_cont, genome_quality_file):
        """Identify trusted genomes.

        Parameters
        ----------
        genome_ids : iterable
            Genomes to inspect to see if should be treated as trusted.
        trusted_comp : float
            Minimum completeness to trust genome for marker set inference.
        trusted_cont : float
            Maximum contamination to trust genome for marker set inference
        genome_quality_file : str
            File specifying completeness and contamination of genomes.

        Returns
        -------
        set
            Unique id of trusted genomes.
        """

        # determine completeness and contamination of each genome
        genome_quality = {}
        with open(genome_quality_file) as f:
            header = f.readline().split('\t')
            comp_index = header.index('Completeness')
            cont_index = header.index('Contamination')

            for line in f:
                line_split = line.split('\t')

                genome_id = line_split[0]
                comp = float(line_split[comp_index])
                cont = float(line_split[cont_index])

                genome_quality[genome_id] = [comp, cont]

        trusted_genome_ids = set()
        for genome_id in genome_ids:
            comp, cont = genome_quality.get(genome_id, (0, 100))

            if comp >= (trusted_comp * 100) and cont <= (trusted_cont * 100):
                trusted_genome_ids.add(genome_id)

        return trusted_genome_ids

    def _genes_in_genomes(self, genome_ids):
        """Get genes within genomes.

        Parameters
        ----------
        genome_ids : iterable
            Genomes of interest.

        Returns
        -------
        d[genome_id][family_id] -> [(gene_id_1, bitscore), ..., (gene_id_N, bitscore)]
            Genes within each genome.
        """

        genes_in_genome = {}
        for genome_id in genome_ids:

            marker_id_to_gene_id = defaultdict(list)
            with open(os.path.join(self.img_genome_dir, genome_id, genome_id + self.pfam_extension)) as f:
                f.readline()
                for line in f:
                    line_split = line.split('\t')
                    pfam_id = line_split[self.pfam_annotation_index]
                    pfam_id = pfam_id.replace('pfam', 'PF')
                    bitscore = float(line_split[self.pfam_bitscore_index])
                    marker_id_to_gene_id[pfam_id].append((line_split[0], bitscore))

            with open(os.path.join(self.img_genome_dir, genome_id, genome_id + self.tigr_extension)) as f:
                f.readline()
                for line in f:
                    line_split = line.split('\t')
                    bitscore = float(line_split[self.tigr_bitscore_index])
                    marker_id_to_gene_id[line_split[self.tigr_annotation_index]].append((line_split[0], bitscore))

            genes_in_genome[genome_id] = marker_id_to_gene_id

        return genes_in_genome

    def _read_img_gene_table(self, table, genome_ids, extension, protein_family_index):
        """Read IMG gene annotations from table.

        Parameters
        ----------
        table : defaultdict(lambda: defaultdict(set))
            Table to populate with gene information.
        genome_ids : iterable
            Genomes of interest.
        extension : str
            Extension of file containing gene annotations.
        protein_family_index : int
            Index of protein family annotation in gene annotation table.
        """

        for genome_id in genome_ids:
            gene_id_to_family_ids = defaultdict(set)
            with open(os.path.join(self.img_genome_dir, genome_id, genome_id + extension)) as f:
                f.readline()

                for line in f:
                    line_split = line.split('\t')

                    gene_id = line_split[0]
                    protein_family = line_split[protein_family_index]
                    protein_family = protein_family.replace('pfam', 'PF')

                    # IMG may annotate multiple parts of a gene as coming
                    # from the same protein family (Pfam, TIGRFAMs), but this
                    # should only count as 1 gene having this annotation
                    if protein_family not in gene_id_to_family_ids[gene_id]:
                        gene_id_to_family_ids[gene_id].add(protein_family)
                        table[protein_family][genome_id].add(gene_id)

    def _img_gene_count_table(self, genome_ids):
        """Get IMG Pfam and TIGRFAMs annotations for genomes.

        Parameters
        ----------
        genome_ids : iterable
            Genomes of interest.

        Returns
        -------
        d[family_id][genome_id] -> set([gene_id_1, ..., gene_id_N])
            Gene location of protein families within each genome.
        """

        table = defaultdict(lambda: defaultdict(set))
        self._read_img_gene_table(table, genome_ids, self.pfam_extension, self.pfam_annotation_index)
        self._read_img_gene_table(table, genome_ids, self.tigr_extension, self.tigr_annotation_index)

        return table

    def _marker_genes(self, genome_ids, gene_count_table, ubiquity_threshold, single_copy_threshold, output_file):
        """Identify genes meeting ubiquity and single-copy thresholds.

        Parameters
        ----------
        genome_ids : iterable
            Genomes of interest.
        gene_count_table : d[family_id][genome_id] -> set([gene_id_1, ..., gene_id_N])
            Gene location of protein families within each genome.
        ubiquity_threshold : float
            Threshold for defining a ubiquitous marker genes [0, 1].
        single_copy_threshold : float
            Threshold for defining a single-copy marker gene [0, 1].
        output_file : str
            Output file indicating ubiquity and single-copy values for all genes.

        Returns
        -------
        dict: d[protein family] -> (ubiquity, single copy)
            Marker genes satisfying selection criteria.
        """

        if ubiquity_threshold > 1 or single_copy_threshold > 1:
            print '[Warning] Looks like degenerate threshold.'

        fout = open(output_file, 'w')
        fout.write('Model accession\tUbiquity\tSingle copy\n')

        # find genes meeting ubiquity and single-copy thresholds
        markers = {}
        for protein_family, genes_in_genomes in gene_count_table.iteritems():
            ubiquity = 0
            single_copy = 0

            for genome_id in genome_ids:
                count = len(genes_in_genomes.get(genome_id, []))

                if count > 0:
                    ubiquity += 1

                if count == 1:
                    single_copy += 1

            u = ubiquity * 100.0 / len(genome_ids)
            s = single_copy * 100.0 / ubiquity
            fout.write('%s\t%.1f\t%.1f\n' % (protein_family, u, s))

            if ubiquity >= (ubiquity_threshold * len(genome_ids)) and single_copy >= (single_copy_threshold * ubiquity):
                markers[protein_family] = (u, s)

        fout.close()

        return markers

    def _identify_redundant_hmms(self, marker_genes, gene_count_table, redundancy, output_file):
        """Identify HMMs that consistently hit the same gene.

        This function identifies redundant HMMs both between and within
        the set of Pfam and TIGRFAMs HMMs. Redundancy between these two
        protein families is common. Redundancy within the TIGRFAMs HMMs
        is also common as there are a number of lineage-specific HMMs
        (e.g., archaeal and universal). Preference if given to TIGRFAMs
        HMMs over Pfam HMMs as the former often model full genes instead
        of just domains.

        Parameters
        ----------
        marker_genes : iterable
            Marker genes to process for redundancy.
        gene_count_table : d[family_id][genome_id] -> set([gene_id_1, ..., gene_id_N])
            Gene location of protein families within each genome.
        redundancy : float
            Threshold for declaring HMMs redundant.
        output_file : str
            Output file to contain list of HMMs deemed to be redunant.

        Returns
        -------
        set
            Marker genes identified as being redundant.
        """

        if redundancy < 1:
            print '[Warning] Looks like redundancy threshold is degenerate.'

        fout = open(output_file, 'w')
        fout.write('Kept marker\tRedundant marker\n')

        marker_gene_list = list(marker_genes)

        # count number of times HMMs hit the same gene
        redundancy_count = defaultdict(lambda: defaultdict(int))
        for i in xrange(0, len(marker_gene_list)):
            marker_gene_i = marker_gene_list[i]
            genes_in_genomes_i = gene_count_table[marker_gene_i]

            for j in xrange(i + 1, len(marker_genes)):
                marker_gene_j = marker_gene_list[j]
                genes_in_genomes_j = gene_count_table[marker_gene_j]

                for genome_id in genes_in_genomes_i:
                    if genome_id in genes_in_genomes_j:
                        if genes_in_genomes_i[genome_id].intersection(genes_in_genomes_j[genome_id]):
                            redundancy_count[marker_gene_i][marker_gene_j] += 1

        # Identify HMMs consistently hitting the same gene across genomes.
        #
        # Note that the following sets of redundant families is at least possible:
        #  X,Y
        #  X,Z
        #
        # It is unclear what to do in this case since, in general, we would expect
        # Y to also be redundant with Z. The following always resolves each redundant
        # pair in order giving preference to TIGRFAMs and HMMs with lower numbers. This
        # will NOT always result in the largest possible set of HMMs (i.e., Y, Z may
        # be removed when one could just remove X), but this seems fair since it is unclear
        # how to resolve such situations.
        hmms_to_remove = set()
        for marker_gene_i in redundancy_count:
            for marker_gene_j, count in redundancy_count[marker_gene_i].iteritems():
                if count > redundancy:
                    if marker_gene_i in hmms_to_remove or marker_gene_j in hmms_to_remove:
                        # marker gene from this redundant pair is already marked for removal
                        continue

                    # preferentially discard PFAM models
                    if 'PF' in marker_gene_i and not 'PF' in marker_gene_j:
                        hmms_to_remove.add(marker_gene_i)
                        fout.write('%s\t%s\n' % (marker_gene_j, marker_gene_i))
                    elif not 'pfam' in marker_gene_i and 'PF' in marker_gene_j:
                        hmms_to_remove.add(marker_gene_j)
                        fout.write('%s\t%s\n' % (marker_gene_i, marker_gene_j))
                    elif 'PF' in marker_gene_i and 'PF' in marker_gene_j:
                        # take Pfam model with lowest number as these tend
                        # to encode better known protein families
                        pfam_num_i = int(marker_gene_i.replace('PF', ''))
                        pfam_num_j = int(marker_gene_j.replace('PF', ''))
                        if pfam_num_i > pfam_num_j:
                            hmms_to_remove.add(marker_gene_i)
                            fout.write('%s\t%s\n' % (marker_gene_j, marker_gene_i))
                        else:
                            hmms_to_remove.add(marker_gene_j)
                            fout.write('%s\t%s\n' % (marker_gene_i, marker_gene_j))
                    else:
                        # take TIGRFAMs model with lowest number as these
                        # tend to be more universal and/or to encode better
                        # known protein families
                        tigr_num_i = int(marker_gene_i.replace('TIGR', ''))
                        tigr_num_j = int(marker_gene_j.replace('TIGR', ''))
                        if tigr_num_i > tigr_num_j:
                            hmms_to_remove.add(marker_gene_i)
                            fout.write('%s\t%s\n' % (marker_gene_j, marker_gene_i))
                        else:
                            hmms_to_remove.add(marker_gene_j)
                            fout.write('%s\t%s\n' % (marker_gene_i, marker_gene_j))

        fout.close()

        return hmms_to_remove

    def _fetch_marker_models(self, marker_genes, output_model_dir):
        """Save PFAM and TIGRFAM marker genes into individual model files.

        Parameters
        ----------
        marker_genes : iterable
            Marker genes to process.
        output_model_dir : str
            Directory to store HMM models.
        """

        marker_id_to_name = {}
        for line in open(self.pfam_model_file):
            if 'NAME' in line:
                name = line.split()[1].rstrip()
            elif 'ACC' in line:
                acc = line.split()[1].rstrip()
                marker_id = acc[0:acc.rfind('.')]
                marker_id_to_name[marker_id] = name

        for marker_id in marker_genes:
            if 'PF' in marker_id:
                os.system('hmmfetch ' + self.pfam_model_file + ' ' + marker_id_to_name[marker_id] + ' > ' + os.path.join(output_model_dir, marker_id + '.hmm'))
            else:
                model_file = os.path.join(self.tigrfams_model_dir, marker_id + '.HMM')
                os.system('hmmfetch ' + model_file + ' ' + marker_id + ' > ' + os.path.join(output_model_dir, marker_id + '.hmm'))

    def identify_marker_genes(self, ingroup_file,
                            trusted_completeness, trusted_contamination,
                            ubiquity_threshold, single_copy_threshold, redundancy,
                            valid_marker_genes,
                            output_msa_dir, output_model_dir):
        """Identify ubiquitous, single-copy marker genes.

        Parameters
        ----------
        ingroup_file : str
            File specifying unique ids of ingroup genomes.
        trusted_comp : float
            Minimum completeness to trust genome for marker set inference.
        trusted_cont : float
            Maximum contamination to trust genome for marker set inference.
        ubiquity_threshold : float
            Threshold for defining ubiquity marker genes.
        single_copy_threshold : float
            Threshold for defining a single-copy marker gene.
        redundancy : float
            Threshold for declaring HMMs redundant.
        valid_marker_genes : iterable
            Restrict marker set to genes within this set.
        output_msa_dir : str
            Directory to store multiple sequence alignment of marker genes.
        output_model_dir : str
            Directory to store HMMs of marker genes.
        """

        # read genomes within the ingroup
        ingroup_ids, img_genome_ids, user_genome_ids = read_genome_ids(ingroup_file)
        self.logger.info('    Ingroup genomes: %d' % len(ingroup_ids))
        self.logger.info('      IMG genomes: %d' % len(img_genome_ids))
        self.logger.info('      User genomes: %d' % len(user_genome_ids))

        # get trusted genomes within the ingroup
        trusted_genome_ids = self._trusted_genomes(img_genome_ids, trusted_completeness, trusted_contamination, self.genome_quality_file)
        self.logger.info('    Trusted ingroup genomes: %d' % len(trusted_genome_ids))

        # identify marker genes
        self.logger.info('')
        self.logger.info('  Identifying marker genes.')
        gene_stats_file = os.path.join(output_model_dir, '..', 'gene_stats.all.tsv')
        gene_count_table = self._img_gene_count_table(trusted_genome_ids)
        marker_gene_stats = self._marker_genes(trusted_genome_ids, gene_count_table, ubiquity_threshold, single_copy_threshold, gene_stats_file)
        marker_genes = set(marker_gene_stats.keys())

        if valid_marker_genes:
            self.logger.info('    Restricting %d identified markers to specified marker set.' % len(marker_genes))
            marker_genes = marker_genes.intersection(valid_marker_genes)
        self.logger.info('    Ubiquitous, single-copy marker genes: %d' % len(marker_genes))

        redundancy_out_file = os.path.join(output_model_dir, '..', 'redundant_markers.tsv')
        redundancy = redundancy * len(trusted_genome_ids)
        redundant_hmms = self._identify_redundant_hmms(marker_genes, gene_count_table, redundancy, redundancy_out_file)
        marker_genes = marker_genes - redundant_hmms
        self.logger.info('      Markers identified as redundant: %d' % len(redundant_hmms))
        self.logger.info('      Ubiquitous, single-copy marker genes: %d' % len(marker_genes))

        # get HMM for each marker gene
        self.logger.info('')
        self.logger.info('  Fetching HMM for each marker genes.')
        self._fetch_marker_models(marker_genes, output_model_dir)

        # get mapping of marker ids to gene ids for each genome
        self.logger.info('  Determining genes in genomes of interest.')
        genes_in_genomes = self._genes_in_genomes(trusted_genome_ids)

        # align gene sequences and infer gene trees
        self.logger.info('  Aligning marker genes:')
        align_markers = AlignMarkers(self.img_genome_dir, self.cpus)
        align_markers.run(trusted_genome_ids, marker_genes, genes_in_genomes, output_msa_dir, output_model_dir)

        return len(ingroup_ids), len(img_genome_ids), len(user_genome_ids), trusted_genome_ids, marker_gene_stats, marker_genes

    def infer_gene_trees(self, msa_dir, output_dir, extension):
        """Infer gene trees.

        Parameters
        ----------
        msa_dir : str
            Directory containing multiple sequence alignment of marker genes.
        output_dir : str
            Directory to store gene trees.
        extension : str
            Extension of multiple sequence alignment files.
        """

        files = os.listdir(msa_dir)
        msa_files = []
        for f in files:
            if f.endswith(extension):
                msa_file = os.path.join(msa_dir, f)
                msa_files.append(msa_file)

                # replace any '*' amino acids with an 'X' as many downstream programs do not like asterisk
                fin = open(msa_file)
                data = fin.readlines()
                fin.close()

                fout = open(msa_file, 'w')
                for line in data:
                    if line[0] != '>':
                        line = line.replace('*', 'X')
                    fout.write(line)
                fout.close()

        fasttree = FastTree(1)
        fasttree.parallel_run(msa_files, 'prot', 'wag', output_dir, self.cpus)

        # create gene tree without gene ids for visualization in ARB
        for msa_file in msa_files:
            tree_prefix = ntpath.basename(msa_file)
            if '.' in tree_prefix:
                tree_prefix = tree_prefix[0:tree_prefix.find('.')]

            gene_tree_file = os.path.join(output_dir, tree_prefix + '.tree')
            gene_tree = dendropy.Tree.get_from_path(gene_tree_file, schema='newick', rooting='force-unrooted', preserve_underscores=True)

            # rename nodes to contain only genome id
            for node in gene_tree.leaf_nodes():
                genome_id = node.taxon.label.split(DefaultValues.SEQ_CONCAT_CHAR)[0]
                node.taxon.label = genome_id

            output_tree_file = os.path.join(output_dir, tree_prefix + '.genome_ids.tree')
            gene_tree.write_to_path(output_tree_file, schema='newick', suppress_rooting=True, unquoted_underscores=True)
