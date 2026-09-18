"""Conservative, evidence-carrying projections of supported metadata into a graph.

No network calls, language-model extraction, or inference from free-text descriptions.
"""
import hashlib
import re
from collections import Counter
from urllib.parse import quote

TYPES = {
    "study": "Studies", "sample": "Samples", "sequence": "Sequences",
    "publication": "Publications", "organism": "Organisms", "disease": "Diseases",
    "repository": "Repositories", "program": "NIAID programs",
}
PATTERNS = {
    "study": r"(?:PRJ[NED][AB]\d+|[SED]RP\d+)",
    "sample": r"(?:SAM[NED][A-Z]?\d+|[SED]RS\d+)",
    "sequence": r"(?:GC[AF]_\d+(?:\.\d+)?|[SED]R[RX]\d+|[A-Z]{1,4}(?:_[A-Z]{0,4})?\d{5,}(?:\.\d+)?)",
}
MISSING = {"", "-", "na", "n/a", "none", "null", "unknown", "missing", "not provided", "not applicable", "not collected", "not available", "unspecified"}


def text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def meaningful(value):
    value = text(value)
    return value if value.casefold() not in MISSING else ""


def tokens(value, kind):
    if isinstance(value, list):
        return list(dict.fromkeys(t for item in value[:100] for t in tokens(item, kind)))
    if isinstance(value, dict):
        value = value.get("accession") or value.get("_key") or value.get("id")
    return [part for part in re.split(r"[,;|\s]+", text(value))
            if re.fullmatch(PATTERNS[kind], part)
            and not (kind == "sequence" and any(re.fullmatch(PATTERNS[other], part) for other in ("study", "sample")))]


class Graph:
    def __init__(self, max_nodes=400, max_edges=800):
        self.nodes = {}
        self.edges = {}
        self.max_nodes, self.max_edges = max_nodes, max_edges
        self.truncated = False
        self.unsupported = 0
        self.record = None
        self.record_supported = False

    def evidence(self, path, value, basis="explicit field"):
        path = path.replace("/genome.*/", "/genome.")
        return {"record_id": self.record["id"], "line": self.record["line_number"],
                "path": path, "value": text(value)[:300], "basis": basis}

    def node(self, kind, identifier, label=None, url=None):
        identifier = meaningful(identifier)
        if not identifier:
            return None
        self.record_supported = True
        key = kind + ":" + identifier
        if key not in self.nodes:
            if len(self.nodes) >= self.max_nodes:
                self.truncated = True
                return None
            self.nodes[key] = {"id": key, "type": kind, "identifier": identifier,
                               "label": (meaningful(label) or identifier)[:200], "url": url, "records": []}
        node = self.nodes[key]
        if label and node["label"] == identifier:
            node["label"] = text(label)[:200]
        if self.record["id"] not in node["records"] and len(node["records"]) < 8:
            node["records"].append(self.record["id"])
        return key

    def edge(self, source, target, relation, path, value, status="reported", basis="explicit field"):
        if not source or not target or source == target:
            return
        key = (source, target, relation, status)
        if key not in self.edges:
            if len(self.edges) >= self.max_edges:
                self.truncated = True
                return
            self.edges[key] = {"id": "edge-" + hashlib.sha256(repr(key).encode()).hexdigest()[:16],
                               "source": source, "target": target, "relation": relation,
                               "status": status, "evidence": []}
        evidence = self.evidence(path, value, basis)
        entries = self.edges[key]["evidence"]
        if evidence not in entries and len(entries) < 5:
            entries.append(evidence)

    def entity(self, kind, identifier, path, label=None):
        identifier = text(identifier)
        repository, url = None, None
        if identifier.startswith("PRJ"):
            repository, url = "BioProject", "https://www.ncbi.nlm.nih.gov/bioproject/" + identifier
        elif identifier.startswith("SAM"):
            repository, url = "BioSample", "https://www.ncbi.nlm.nih.gov/biosample/" + identifier
        elif re.fullmatch(r"[SED]R[PSRX]\d+", identifier):
            repository, url = "SRA / INSDC", "https://www.ncbi.nlm.nih.gov/sra/?term=" + identifier
        elif identifier.startswith(("GCA_", "GCF_")):
            repository, url = "NCBI Assembly", "https://www.ncbi.nlm.nih.gov/datasets/genome/" + identifier + "/"
        elif kind == "sequence":
            repository, url = "GenBank / RefSeq", "https://www.ncbi.nlm.nih.gov/nuccore/" + identifier
        elif identifier.startswith("PMID:"):
            repository, url = "PubMed", "https://pubmed.ncbi.nlm.nih.gov/" + identifier[5:] + "/"
        elif identifier.startswith("DOI:"):
            repository, url = "DOI", "https://doi.org/" + quote(identifier[4:], safe="/")
        elif identifier.startswith("NCBITaxon:"):
            repository, url = "NCBI Taxonomy", "https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=" + identifier[10:]
        node = self.node(kind, identifier, label, url)
        if repository:
            repo = self.node("repository", repository)
            self.edge(node, repo, "registered in", path, identifier, "derived", "repository derived from identifier namespace")
        return node

    def refs(self, value, kind, path):
        if isinstance(value, list) and len(value) > 100:
            self.truncated = True
        return [self.entity(kind, token, path) for token in tokens(value, kind)]

    def organism(self, source, taxon, name, path):
        taxon = text(taxon)
        name = meaningful(name)
        if taxon.isdecimal() and int(taxon) > 0:
            node = self.entity("organism", "NCBITaxon:" + taxon, path, name or "Taxon " + taxon)
        elif name:
            node = self.node("organism", "name:" + name.casefold(), name)
        else:
            return
        self.edge(source, node, "organism", path, taxon or name)

    def disease(self, source, value, path, status="reported", term_id=None):
        values = value if isinstance(value, list) else [value]
        if len(values) > 50:
            self.truncated = True
        for value in values[:50]:
            name = meaningful(value)
            if not name:
                continue
            if len(name) > 300:
                self.truncated = True
                continue
            node = self.node("disease", term_id or "reported:" + name.casefold(), name)
            self.edge(source, node, "proposed disease" if status == "proposed" else "reported disease", path, term_id or name, status)

    def publications(self, source, value, path):
        items = value if isinstance(value, list) else [value]
        if len(items) > 100:
            self.truncated = True
        for i, item in enumerate(items[:100]):
            p = f"{path}/{i}" if isinstance(value, list) else path
            label = None
            if isinstance(item, dict):
                label = item.get("title")
                db = text(item.get("db")).casefold()
                if item.get("pmid"):
                    item = "PMID:" + text(item["pmid"])
                elif item.get("doi"):
                    item = "DOI:" + text(item["doi"])
                elif db in {"pubmed", "pmid"}:
                    item = "PMID:" + text(item.get("id"))
                elif db == "doi":
                    item = "DOI:" + text(item.get("id"))
                else:
                    continue
            for part in re.split(r"[,;\s]+", text(item)):
                match = re.fullmatch(r"(?:PMID:|https://pubmed.ncbi.nlm.nih.gov/)?(\d+)/?", part, re.I)
                if match:
                    identifier = "PMID:" + match[1]
                elif re.fullmatch(r"(?:DOI:|https://doi.org/)?10\.\d{4,9}/\S+", part, re.I):
                    identifier = "DOI:" + re.sub(r"^(?:DOI:|https://doi.org/)", "", part, flags=re.I)
                else:
                    continue
                publication = self.entity("publication", identifier, p, label)
                self.edge(source, publication, "cites", p, identifier)

    def programs(self, source, obj, path):
        # An institutional mention or grant identifier alone is not a program assignment.
        key = "niaid_program" if obj.get("niaid_program") else "niaid_programs"
        value = obj.get(key)
        if value:
            if isinstance(value, list) and len(value) > 100:
                self.truncated = True
            for item in (value if isinstance(value, list) else [value])[:100]:
                name = meaningful(item.get("name") if isinstance(item, dict) else item)
                if name:
                    program = self.node("program", "NIAID:" + name.casefold(), name)
                    self.edge(source, program, "reported NIAID program", path + "/" + key, name)

    def objects(self, obj, names, prefix=""):
        for name in names:
            value = obj.get(name)
            if isinstance(value, dict):
                yield value, prefix + "/" + name
            elif isinstance(value, list):
                if len(value) > 100:
                    self.truncated = True
                for i, item in enumerate(value[:100]):
                    if isinstance(item, dict):
                        yield item, f"{prefix}/{name}/{i}"

    def sample(self, obj, path):
        ids = tokens(obj.get("accession") or obj.get("_key") or obj.get("record_id"), "sample")
        if not ids:
            return None
        sample_key = next(k for k in ("accession", "_key", "record_id") if obj.get(k))
        sample = self.entity("sample", ids[0], path + "/" + sample_key, obj.get("title"))
        desc = obj.get("description") if isinstance(obj.get("description"), dict) else {}
        org = desc.get("organism") if isinstance(desc.get("organism"), dict) else {}
        tax_path = "/taxon_id" if obj.get("taxon_id") else "/description/organism/taxId" if org.get("taxId") else "/taxonomy_name" if obj.get("taxonomy_name") else "/description/organism/organismName"
        self.organism(sample, obj.get("taxon_id") or org.get("taxId"), obj.get("taxonomy_name") or org.get("organismName"), path + tax_path)
        project_key = "bioprojects" if obj.get("bioprojects") else "bioproject_accession"
        for study in self.refs(obj.get(project_key), "study", path + "/" + project_key):
            self.edge(sample, study, "belongs to study", path + "/" + project_key, self.nodes[study]["identifier"] if study else "")
        aliases = [(alias, path + "/sra_sample") for alias in tokens(obj.get("sra_sample"), "sample")]
        for item, p in self.objects(obj, ["id_recs"], path):
            if text(item.get("db")).casefold() == "sra":
                aliases.extend((alias, p + "/id") for alias in tokens(item.get("id"), "sample"))
        for alias, alias_path in dict.fromkeys(aliases):
            other = self.entity("sample", alias, alias_path)
            self.edge(sample, other, "same sample identifier", alias_path, alias)
        for key in ["disease", "hostDisease", "host_disease"]:
            self.disease(sample, obj.get(key), path + "/" + key)
        for item, p in self.objects(obj, ["attributes", "attribute_recs"], path):
            key = text(item.get("harmonized_name") or item.get("attribute_name") or item.get("name")).casefold().replace(" ", "_")
            if key in {"disease", "host_disease"}:
                self.disease(sample, item.get("value"), p + "/value")
        self.programs(sample, obj, path)
        return sample

    def assembly(self, genome, path):
        accession_key = next((k for k in ("accession", "currentAccession", "assembly_accession") if genome.get(k)), "accession")
        accession = genome.get(accession_key)
        nodes = self.refs(accession, "sequence", path + "/" + accession_key)
        if not nodes:
            return None
        primary = nodes[0]
        info = genome.get("assemblyInfo") if isinstance(genome.get("assemblyInfo"), dict) else {}
        sample_path = path + "/biosample_accession"
        samples = [(s, sample_path) for s in self.refs(genome.get("biosample_accession"), "sample", sample_path)]
        if isinstance(info.get("biosample"), dict):
            samples.append((self.sample(info["biosample"], path + "/assemblyInfo/biosample"), path + "/assemblyInfo/biosample/accession"))
        for sample, sample_path in samples:
            self.edge(primary, sample, "sequenced from", sample_path, self.nodes[sample]["identifier"] if sample else "")
        for key, value in [("/bioproject_accession", genome.get("bioproject_accession")), ("/assemblyInfo/bioprojectAccession", info.get("bioprojectAccession"))]:
            for study in self.refs(value, "study", path + key):
                self.edge(primary, study, "belongs to study", path + key, value)
        paired_obj = info.get("pairedAssembly") if isinstance(info.get("pairedAssembly"), dict) else {}
        paired = genome.get("pairedAccession") or paired_obj.get("accession")
        paired_path = path + ("/pairedAccession" if genome.get("pairedAccession") else "/assemblyInfo/pairedAssembly/accession")
        for other in self.refs(paired, "sequence", paired_path):
            self.edge(primary, other, "paired assembly", paired_path, paired)
        for key in ["genbank_accessions", "refseq_accessions", "sra_accession"]:
            for sequence in self.refs(genome.get(key), "sequence", path + "/" + key):
                self.edge(primary, sequence, "linked sequence", path + "/" + key, self.nodes[sequence]["identifier"] if sequence else "")
        org = genome.get("organism") if isinstance(genome.get("organism"), dict) else {}
        tax_path = "/organism/taxId" if org.get("taxId") else "/taxon_id" if genome.get("taxon_id") else "/organism/organismName" if org.get("organismName") else "/organism_name" if genome.get("organism_name") else "/species"
        self.organism(primary, org.get("taxId") or genome.get("taxon_id"), org.get("organismName") or genome.get("organism_name") or genome.get("species"), path + tax_path)
        # Disease is attached to this record's assembly; it is not a general species claim.
        for key in ["disease", "host_disease"]:
            self.disease(primary, genome.get(key), path + "/" + key)
        pub_key = "publication" if genome.get("publication") else "publications"
        self.publications(primary, genome.get(pub_key), path + "/" + pub_key)
        self.programs(primary, genome, path)
        return primary

    def add_record(self, record, obj):
        self.record = record
        self.record_supported = False
        prefix = ""
        proposal = obj.get("proposal") if isinstance(obj.get("proposal"), dict) else obj if "terms" in obj else None
        if isinstance(obj.get("source_record"), dict):
            obj = obj["source_record"]
            prefix = "/source_record"
        primary = None
        for genome, path in self.objects(obj, ["genome", "genomes", "bvbrc"], prefix):
            node = self.assembly(genome, path)
            primary = primary or node
            if path.endswith("/bvbrc") and node:
                repo = self.node("repository", "BV-BRC")
                self.edge(node, repo, "metadata supplied by", path, genome.get("genome_id") or genome.get("bvbrc_id"), "reported")
        flat = {k.removeprefix("genome."): v for k, v in obj.items() if k.startswith("genome.")}
        if flat:
            primary = self.assembly(flat, prefix + "/genome.*") or primary
        if obj.get("assembly_accession"):
            primary = self.assembly(obj, prefix) or primary
        for sample, path in self.objects(obj, ["biosample", "biosamples"], prefix):
            primary = self.sample(sample, path) or primary
        for study, path in self.objects(obj, ["bioproject", "bioprojects"], prefix):
            ids = tokens(study.get("accession") or study.get("_key"), "study")
            if ids:
                accession_key = "accession" if study.get("accession") else "_key"
                node = self.entity("study", ids[0], path + "/" + accession_key, study.get("title"))
                self.publications(node, study.get("publications"), path + "/publications")
                self.programs(node, study, path)
        for exp, path in self.objects(obj, ["sra_experiments", "sra-experiment", "sra_runs"], prefix):
            accession_key = next((k for k in ("accession", "run_accession", "experiment_accession") if exp.get(k)), "accession")
            accession = exp.get(accession_key)
            ids = tokens(accession, "sequence")
            if not ids:
                continue
            node = self.entity("sequence", ids[0], path + "/" + accession_key, exp.get("title"))
            for field, kind, relation in [("sample_accession", "sample", "sequenced from"), ("study_accession", "study", "belongs to study"), ("experiment_accession", "sequence", "run of experiment")]:
                for target in self.refs(exp.get(field), kind, path + "/" + field):
                    self.edge(node, target, relation, path + "/" + field, exp.get(field))
        # Root-level metadata needs a root-level identity; a nested object is not enough.
        primary = None
        # Engine input/output rows carry sample IDs instead of nested NCBI objects.
        original = obj.get("original_metadata") if isinstance(obj.get("original_metadata"), dict) else obj
        rid = obj.get("record_id") or original.get("record_id")
        identity_path = prefix + ("/original_metadata" if not obj.get("record_id") and original is not obj else "") + "/record_id"
        sample_ids = tokens(rid, "sample")
        if sample_ids:
            primary = self.entity("sample", sample_ids[0], identity_path)
        elif tokens(rid, "sequence"):
            primary = self.entity("sequence", tokens(rid, "sequence")[0], identity_path)
        extras = original.get("extras") if isinstance(original.get("extras"), dict) else {}
        self.organism(primary, extras.get("taxonomy_id"), extras.get("organism"), prefix + ("/original_metadata" if original is not obj else "") + "/extras") if primary else None
        if primary:
            self.disease(primary, original.get("disease"), prefix + "/original_metadata/disease" if original is not obj else prefix + "/disease")
            attributes = extras.get("attributes") if isinstance(extras.get("attributes"), dict) else {}
            disease_key = "host_disease" if attributes.get("host_disease") else "disease"
            original_prefix = prefix + ("/original_metadata" if original is not obj else "")
            self.disease(primary, attributes.get(disease_key), original_prefix + "/extras/attributes/" + disease_key)
            self.programs(primary, original, original_prefix)
        if proposal and primary:
            # A proposal may only attach to the same explicit record identifier.
            proposal_id = proposal.get("record_id")
            if proposal_id and proposal_id == rid:
                candidates = proposal.get("candidate_curies")
                candidates = candidates if isinstance(candidates, list) else []
                terms = proposal.get("terms")
                terms = terms if isinstance(terms, list) else []
                if len(terms) > 100:
                    self.truncated = True
                for i, term in enumerate(terms[:100]):
                    if not isinstance(term, dict):
                        continue
                    identifier = text(term.get("term_id"))
                    if term.get("ontology") == "MONDO" and re.fullmatch(r"MONDO:\d+", identifier) and identifier in candidates:
                        self.disease(primary, term.get("label") or identifier, ("/proposal" if obj is not proposal else "") + f"/terms/{i}", "proposed", identifier)
        if not self.record_supported:
            self.unsupported += 1

    def result(self):
        counts = Counter(node["type"] for node in self.nodes.values())
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values()),
                "types": [{"id": key, "label": label, "count": counts[key]} for key, label in TYPES.items()],
                "truncated": self.truncated, "unsupported_records": self.unsupported}
