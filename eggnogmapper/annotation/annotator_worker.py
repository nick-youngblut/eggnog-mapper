##
## CPCantalapiedra 2021

from collections import Counter

from ..emapperException import EmapperException

from .ncbitaxa.ncbiquery import get_ncbi
from .pfam.pfam_modes import PFAM_TRANSFER_NARROWEST_OG, PFAM_TRANSFER_SEED_ORTHOLOG
from .tax_scopes.tax_scopes import parse_nogs

from .db_sqlite import get_eggnog_db

from . import orthologs as ortho
from . import annota
from . import output

ANNOTATIONS_HEADER = output.ANNOTATIONS_HEADER

##
def annotate_hit_line_mem(arguments):
    eggnog_db = get_eggnog_db(usemem = True)
    ncbi = get_ncbi(usemem = True)

    return annotate_hit_line(arguments, eggnog_db, ncbi)

##
def annotate_hit_line_ondisk(arguments):
    eggnog_db = get_eggnog_db(usemem = False)
    ncbi = get_ncbi(usemem = False)
    ret = annotate_hit_line(arguments, eggnog_db, ncbi)
    return ret

# annotate_hit_line is outside the class because must be pickable
##
def annotate_hit_line(arguments, eggnog_db, ncbi):
    
    hit, annot, seed_ortholog_score, seed_ortholog_evalue, \
        tax_scope_mode, tax_scope_id, \
        target_taxa, target_orthologs, excluded_taxa, \
        go_evidence, go_excluded, \
        pfam_transfer = arguments
    
    try:
        query_name = hit[0]
        best_hit_name = hit[1]
        best_hit_evalue = float(hit[2])
        best_hit_score = float(hit[3])
        
        ##
        # Filter by empty hit, error, evalue and/or score
        if filter_out(best_hit_name, best_hit_evalue, best_hit_score, seed_ortholog_evalue, seed_ortholog_score):
            return None
        
        ##
        # Retrieve OGs (orthologs groups) the hit belongs to
        print("annotator_worker.py:annotate_hit_line")
        print(f"Hit: {best_hit_name}")
        match_nogs = get_member_ogs(best_hit_name, eggnog_db)
        if not match_nogs:
            return None
        print(f"Match nogs: {match_nogs}")
            
        ##
        # Obtain names of OGs, narrowest OG, and the best OG according to tax_scope
        match_nogs_names, narr_og, best_og = parse_nogs(match_nogs, tax_scope_mode, tax_scope_id)
                
        if best_og is None:
            return None
        
        best_og_id, best_og_level, best_og_name = best_og
        best_og_cat, best_og_desc = get_og_description(best_og_id, best_og_level, eggnog_db)

        narr_og_id, narr_og_level, narr_og_name = narr_og
        narr_og_cat, narr_og_desc = get_og_description(narr_og_id, narr_og_level, eggnog_db)

        ##
        # Normalize target_taxa if any
        if target_taxa is not None:
            target_taxa = normalize_target_taxa(target_taxa, ncbi)
        else:
            target_taxa = None

        if excluded_taxa is not None:
            excluded_taxa = normalize_target_taxa(excluded_taxa, ncbi)
        else:
            excluded_taxa = None

        ##
        # Retrieve co-orthologs of seed ortholog
        # annot_levels are used to restrict the speciation events retrieved
        # target_taxa are used to restrict the species from which to retrieve co-ortholog proteins
        annot_levels = set()
        annot_levels.add(best_og_level)
        try:
            all_orthologies, best_OG = ortho.get_member_orthologs(best_hit_name, annot_levels, match_nogs_names, eggnog_db)
            if best_OG is not None:
                best_og_name = best_OG
                best_og_id = best_OG.split("|")[0].split("@")[0]
                best_og_level = best_OG.split("|")[0].split("@")[1]
                if best_og_id == "seed_ortholog":
                    best_og_cat = "-"
                    best_og_desc = "-"
                else:
                    best_og_cat, best_og_desc = get_og_description(best_og_id, best_og_level, eggnog_db)

        except Exception as e:
            # import traceback
            # traceback.print_exc()
            raise e
        else:
            # filter co-orthologs to keep only target_orthologs: "all", "one2one", ...
            orthologs = _filter_orthologs(all_orthologies, target_orthologs, target_taxa, excluded_taxa)

        ##
        # Retrieve annotations of co-orthologs
        if annot == True and orthologs is not None and len(orthologs) > 0:

            annotations = annota.summarize_annotations(orthologs,
                                                       annotations_fields = ANNOTATIONS_HEADER,
                                                       target_go_ev = go_evidence,
                                                       excluded_go_ev = go_excluded,
                                                       eggnog_db = eggnog_db)

            if pfam_transfer == PFAM_TRANSFER_NARROWEST_OG:
                if best_og_level == narr_og_level:
                    narr_orthologies = all_orthologies
                else:
                    narr_annot_levels = set()
                    narr_annot_levels.add(narr_og_level)
                    narr_orthologies, _ = ortho.get_member_orthologs(best_hit_name, narr_annot_levels, match_nogs_names, eggnog_db)

                # filter co-orthologs to keep only target_orthologs: "all", "one2one", ...
                narr_orthologs = _filter_orthologs(narr_orthologies, target_orthologs, target_taxa, excluded_taxa)

                pfam_annotations = eggnog_db.get_pfam_annotations(','.join(['"%s"' % n for n in narr_orthologs]))
                if pfam_annotations is not None and len(pfam_annotations) > 0:
                    annotations["PFAMs"] = Counter()
                    for pfam_annotation in pfam_annotations:
                        annotations["PFAMs"].update([str(x).strip() for x in pfam_annotation[0].split(",")])
                else:
                    annotations["PFAMs"] = Counter()

            elif pfam_transfer == PFAM_TRANSFER_SEED_ORTHOLOG:
                pfam_annotations = eggnog_db.get_pfam_annotations('"'+best_hit_name+'"')
                if pfam_annotations is not None and len(pfam_annotations) > 0:
                    pfam_annotations = Counter(list(pfam_annotations[0][0].split(",")))
                    annotations["PFAMs"] = pfam_annotations
                else:
                    annotations["PFAMs"] = Counter()                    
            else: # pfam_transfer == PFAM_TRANSFER_BEST_OG
                pass

        else:
            annotations = {}

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise EmapperException(f'Error: annotation went wrong for hit {hit}. '+str(e))
    
    return (query_name, best_hit_name, best_hit_evalue, best_hit_score,
            annotations,
            narr_og_name, narr_og_cat, narr_og_desc,
            best_og_name, best_og_cat, best_og_desc,
            match_nogs_names, all_orthologies, orthologs)


def _filter_orthologs(all_orthologies, target_orthologs, target_taxa, excluded_taxa):
    orthologs = sorted(all_orthologies[target_orthologs])
    if excluded_taxa is not None:
        orthologs = [o for o in orthologs if int(o.split(".")[0]) not in excluded_taxa]
    if target_taxa is not None:
        orthologs = [o for o in orthologs if int(o.split(".")[0]) in target_taxa]
    return orthologs
        
##
def filter_out(hit_name, hit_evalue, hit_score, threshold_evalue, threshold_score):
    """
    Filter hit if ERROR, by score or by evalue
    """
    if hit_name == '-' or hit_name == 'ERROR':
        return True
    
    if hit_score < threshold_score or hit_evalue > threshold_evalue:
        return True
    
    return False

##
def normalize_target_taxa(target_taxa, ncbi):
    """
    Receives a list of taxa IDs and/or taxa names and returns a set of expanded taxids numbers
    """
    expanded_taxa = set()
    
    for taxon in target_taxa:
        taxid = ""
        try:
            taxid = int(taxon)
        except ValueError:
            taxid = ncbi.get_name_translator([taxon])[taxon][0]
        else:
            taxon = ncbi.get_taxid_translator([taxid])[taxid]

        if taxid is not None:
            species = ncbi.get_descendant_taxa(taxid, intermediate_nodes = True)
            for sp in species:
                expanded_taxa.add(sp)

    return expanded_taxa


def get_member_ogs(name, eggnog_db):
    ogs = None
    match = eggnog_db.get_member_ogs(name)
    if match:
        ogs = [str(x).strip() for x in match[0].split(',')]
    return ogs


def get_og_description(og, level, eggnog_db):
    best = ['-', '-', '-']
    
    for og, nm, desc, cat in eggnog_db.get_ogs_description(og, level):
        desc = desc.strip()
        if desc and desc != 'N/A' and desc != 'NA':
            best = [nm, cat, desc]
            break
    
    return best[1], best[2]

## END