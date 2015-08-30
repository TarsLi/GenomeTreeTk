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
import sys
import logging

import dendropy

from checkm.util.img import IMG

from genome_tree.defaultValues import DefaultValues

class ConsistencyTest(object):
    def __init__(self):
        self.logger = logging.getLogger()

    def __genomeId(self, taxaLabel):
        if taxaLabel.startswith('IMG_'):
            genomeId = taxaLabel[4:]
        elif '|' in taxaLabel:
            # assume taxa are labeled with the format: genomeId_geneId
            genomeId = taxaLabel.split('|')[0]
        else:
            genomeId = taxaLabel

        return genomeId

    def run(self, geneTreeDir, treeExtension, consistencyThreshold, minTaxaForAverage, outputFile, outputDir):
        # make sure output directory is empty
        if not os.path.exists(outputDir):
            os.makedirs(outputDir)

        files = os.listdir(outputDir)
        for f in files:
            if os.path.isfile(os.path.join(outputDir, f)):
                os.remove(os.path.join(outputDir, f))

        # get TIGRFam info
        descDict = {}
        files = os.listdir('/srv/db/tigrfam/13.0/TIGRFAMs_13.0_INFO')
        for f in files:
            shortDesc = longDesc = ''
            for line in open('/srv/db/tigrfam/13.0/TIGRFAMs_13.0_INFO/' + f):
                lineSplit = line.split('  ')
                if lineSplit[0] == 'AC':
                    acc = lineSplit[1].strip()
                elif lineSplit[0] == 'DE':
                    shortDesc = lineSplit[1].strip()
                elif lineSplit[0] == 'CC':
                    longDesc = lineSplit[1].strip()

            descDict[acc] = [shortDesc, longDesc]

        # get PFam info
        for line in open('/srv/db/pfam/27/Pfam-A.clans.tsv'):
            lineSplit = line.split('\t')
            acc = lineSplit[0]
            shortDesc = lineSplit[3]
            longDesc = lineSplit[4].strip()

            descDict[acc] = [shortDesc, longDesc]

        # get IMG taxonomy
        img = IMG(DefaultValues.IMG_METADATA, DefaultValues.TIGRFAM_TO_PFAM)
        metadata = img.genomeMetadata()
        genomeIdToTaxonomy = {}
        for genomeId, m in metadata.iteritems():
            genomeIdToTaxonomy[genomeId] = m['taxonomy']

        # perform analysis for each tree
        treeFiles = os.listdir(geneTreeDir)
        allResults = {}
        allTaxa = [set([]), set([]), set([])]
        taxaCounts = {}
        avgConsistency = {}
        for treeFile in treeFiles:
            if not treeFile.endswith(treeExtension):
                continue

            tree = dendropy.Tree.get_from_path(os.path.join(geneTreeDir, treeFile), schema='newick', rooting='force-rooted', preserve_underscores=True)

            domainConsistency = {}
            phylaConsistency = {}
            classConsistency = {}
            consistencyDict = [domainConsistency, phylaConsistency, classConsistency]

            # get abundance of taxa at different taxonomic ranks
            totals = [{}, {}, {}]
            leaves = tree.leaf_nodes()
            totalValidLeaves = 0

            for leaf in leaves:
                genomeId = self.__genomeId(leaf.taxon.label)

                if genomeId not in metadata:
                    print '[Error] Genome is missing metadata: ' + genomeId
                    sys.exit()

                totalValidLeaves += 1
                taxonomy = genomeIdToTaxonomy[genomeId]
                for r in xrange(0, 3):
                    totals[r][taxonomy[r]] = totals[r].get(taxonomy[r], 0) + 1
                    consistencyDict[r][taxonomy[r]] = 0
                    allTaxa[r].add(taxonomy[r])

            taxaCounts[treeFile] = [totalValidLeaves, totals[0].get('Bacteria', 0), totals[0].get('Archaea', 0)]

            # find highest consistency nodes (congruent descendant taxa / (total taxa + incongruent descendant taxa))
            internalNodes = tree.internal_nodes()
            for node in internalNodes:
                leaves = node.leaf_nodes()

                for r in xrange(0, 3):
                    leafCounts = {}
                    for leaf in leaves:
                        genomeId = self.__genomeId(leaf.taxon.label)
                        taxonomy = genomeIdToTaxonomy[genomeId]
                        leafCounts[taxonomy[r]] = leafCounts.get(taxonomy[r], 0) + 1

                    # calculate consistency for node
                    for taxa in consistencyDict[r]:
                        totalTaxaCount = totals[r][taxa]
                        if totalTaxaCount <= 1 or taxa == 'unclassified':
                            consistencyDict[r][taxa] = 'N/A'
                            continue

                        taxaCount = leafCounts.get(taxa, 0)
                        incongruentTaxa = len(leaves) - taxaCount
                        c = float(taxaCount) / (totalTaxaCount + incongruentTaxa)
                        if c > consistencyDict[r][taxa]:
                            consistencyDict[r][taxa] = c

                        # consider clan in other direction since the trees are unrooted
                        taxaCount = totalTaxaCount - leafCounts.get(taxa, 0)
                        incongruentTaxa = totalValidLeaves - len(leaves) - taxaCount
                        c = float(taxaCount) / (totalTaxaCount + incongruentTaxa)
                        if c > consistencyDict[r][taxa]:
                            consistencyDict[r][taxa] = c

            # write results
            consistencyDir = os.path.join(outputDir, 'consistency')
            if not os.path.exists(consistencyDir):
                os.makedirs(consistencyDir)
            fout = open(os.path.join(consistencyDir, treeFile + '.results.tsv'), 'w')
            fout.write('Tree')
            for r in xrange(0, 3):
                for taxa in sorted(consistencyDict[r].keys()):
                    fout.write('\t' + taxa)
            fout.write('\n')

            fout.write(treeFile)
            for r in xrange(0, 3):
                for taxa in sorted(consistencyDict[r].keys()):
                    if consistencyDict[r][taxa] != 'N/A':
                        fout.write('\t%.2f' % (consistencyDict[r][taxa] * 100))
                    else:
                        fout.write('\tN/A')
            fout.close()

            # calculate average consistency at each taxonomic rank
            average = []
            for r in xrange(0, 3):
                sumConsistency = []
                for taxa in consistencyDict[r]:
                    if totals[r][taxa] > minTaxaForAverage and consistencyDict[r][taxa] != 'N/A':
                        sumConsistency.append(consistencyDict[r][taxa])

                if len(sumConsistency) > 0:
                    average.append(sum(sumConsistency) / len(sumConsistency))
                else:
                    average.append(0)
            avgConsistency[treeFile] = average
            allResults[treeFile] = consistencyDict

        # print out combined results
        fout = open(outputFile, 'w')
        fout.write('Tree\tShort Desc.\tLong Desc.\tAlignment Length\t# Taxa\t# Bacteria\t# Archaea\tAvg. Consistency\tAvg. Domain Consistency\tAvg. Phylum Consistency\tAvg. Class Consistency')
        for r in xrange(0, 3):
            for t in sorted(allTaxa[r]):
                fout.write('\t' + t)
        fout.write('\n')

        filteredGeneTrees = 0
        retainedGeneTrees = 0
        for treeFile in sorted(allResults.keys()):
            consistencyDict = allResults[treeFile]
            treeId = treeFile[0:treeFile.find('.')]

            fout.write(treeId + '\t' + descDict[treeId][0] + '\t' + descDict[treeId][1])

            # Taxa count
            fout.write('\t' + str(taxaCounts[treeFile][0]) + '\t' + str(taxaCounts[treeFile][1]) + '\t' + str(taxaCounts[treeFile][2]))

            avgCon = 0
            for r in xrange(0, 3):
                avgCon += avgConsistency[treeFile][r]
            avgCon /= 3
            fout.write('\t' + str(avgCon))

            if avgCon >= consistencyThreshold:
                retainedGeneTrees += 1
                os.system('cp ' + os.path.join(geneTreeDir, treeFile) + ' ' + os.path.join(outputDir, treeFile))
            else:
                filteredGeneTrees += 1

            for r in xrange(0, 3):
                fout.write('\t' + str(avgConsistency[treeFile][r]))

            for r in xrange(0, 3):
                for t in sorted(allTaxa[r]):
                    if t in consistencyDict[r]:
                        if consistencyDict[r][t] != 'N/A':
                            fout.write('\t%.2f' % (consistencyDict[r][t] * 100))
                        else:
                            fout.write('\tN/A')
                    else:
                        fout.write('\tN/A')
            fout.write('\n')
        fout.close()

        self.logger.info('    Retained gene trees: %d' % retainedGeneTrees)
        self.logger.info('    Filtered gene trees: %d' % filteredGeneTrees)

        return filteredGeneTrees, retainedGeneTrees
