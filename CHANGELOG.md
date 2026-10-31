# CHANGELOG


## [0.1.5](https://github.com/athril/agentic-target-evidence/compare/v0.1.4...v0.1.5) (2026-10-31)


### Features

* **biology:** compute the perturbation direction from models of the disease ([9a8bf99](https://github.com/athril/agentic-target-evidence/commit/9a8bf99f982179f4abe623ca9796c521d1307ab8))
* **biology:** read model perturbations from statements of the target or its ortholog ([43c0d5c](https://github.com/athril/agentic-target-evidence/commit/43c0d5cd6a8f45813f4f918072e889f6dc59a960))
* **biology:** rule the mechanism axis on failed programmes and knockout data ([bb216c4](https://github.com/athril/agentic-target-evidence/commit/bb216c4d90b0aefd38bc1b32fdabb88b7a40e6dc))
* **biology:** set druggability by rule and give the lens each drug's modality ([1cf3817](https://github.com/athril/agentic-target-evidence/commit/1cf3817d62077e0eef75c7b726312a62127661f5))
* **claims:** build structured records' claims deterministically, LLM for free text only ([072e0e1](https://github.com/athril/agentic-target-evidence/commit/072e0e1408c8246d5e18caa3d63e82a688995921))
* **claims:** type each claim's effect and derive its direction in code ([375634a](https://github.com/athril/agentic-target-evidence/commit/375634aabb9a5157053e18c66c6ef95bb3eacab2))
* **clinical:** attach a publication's results to the trial it names by NCT id ([295fdba](https://github.com/athril/agentic-target-evidence/commit/295fdbaa987f8eb3a87eb2552eb387773599b97e))
* **clinical:** axes and verdict computed from the programmes in the indication ([cbb8b7a](https://github.com/athril/agentic-target-evidence/commit/cbb8b7aa7aa5c76230a4cae755aa54bb95866dac))
* **clinical:** merge trial outcomes reported in publications into the programmes ([24a3af1](https://github.com/athril/agentic-target-evidence/commit/24a3af146eef39aadbf4256028d30cf58bfc8a3f))
* **clinical:** show the lens a programme table and the axes fixed by rule ([b68a5b3](https://github.com/athril/agentic-target-evidence/commit/b68a5b3c672681ee4573df45bcb75a40b10fd2e3))
* **clinicaltrials:** keep hard-endpoint secondaries and deaths per arm ([1d7a2a0](https://github.com/athril/agentic-target-evidence/commit/1d7a2a0e3f6406a80cd5fcc9e65aa1b3f0deb170))
* **clinvar:** read ClinVar's pathogenic calls with the condition each is for ([a7fb7fe](https://github.com/athril/agentic-target-evidence/commit/a7fb7fe3daffd371dcb5c5f8778dadbfaebdd486))
* **decision:** check the therapeutic direction against what drugs did in the indication ([113bff7](https://github.com/athril/agentic-target-evidence/commit/113bff77e2628e639c28376113711eb9d1e3a6e6))
* **decision:** compute the suitability score instead of asking the model ([2d368bd](https://github.com/athril/agentic-target-evidence/commit/2d368bd48db0d3d0ef334d8fde8f0bded2838857))
* **direction:** read the disease's own genetic direction from Open Targets annotations ([f127d0e](https://github.com/athril/agentic-target-evidence/commit/f127d0e217b7460e62ffeb5d4b29d3698c635fda))
* **direction:** weigh literature by study design instead of counting votes ([3819f7b](https://github.com/athril/agentic-target-evidence/commit/3819f7bc455fc9ccee8b89a958edbf4e369492b5))
* **evidence:** a disease scope for text, with the outcomes that count the disease ([6ae9dc7](https://github.com/athril/agentic-target-evidence/commit/6ae9dc7b1b27b9ed3f2040a42ccd6e4d703e0326))
* **evidence:** read the direction of effect from drugs, labels and human genetics ([c0991ac](https://github.com/athril/agentic-target-evidence/commit/c0991acde50ff79c6a597af108ef97eca3f956e7))
* **evidence:** typography-tolerant disease, trait and indication matching ([7811742](https://github.com/athril/agentic-target-evidence/commit/781174238295804ab7cdcc46da0a5960389563a5))
* **extraction:** check model-typed claims against their source and record what was left out ([6c043e5](https://github.com/athril/agentic-target-evidence/commit/6c043e5f79985bd6775fd0c5f7bcb41d10012812))
* **extraction:** type literature claims from statements of grounded sentences ([6466967](https://github.com/athril/agentic-target-evidence/commit/646696766e095f7ebc2dd537f315ef98a3c47b57))
* **extraction:** type the target's change from drug data and two narrow fields ([f33a438](https://github.com/athril/agentic-target-evidence/commit/f33a438abd02db2649a2bc871f9eeb36c35109ce))
* **gene-profile:** a constraint table on its release and transcript ([17f39f2](https://github.com/athril/agentic-target-evidence/commit/17f39f274dc6dc0f2979f5570bf6e5d743efb83a))
* **gene-profile:** a FAERS table that says what reports cannot show ([34e433d](https://github.com/athril/agentic-target-evidence/commit/34e433dfe7aa9455087a5ac3dd92b15396fa56e7))
* **gene-profile:** check numbers and names sentence by sentence ([6b4fe01](https://github.com/athril/agentic-target-evidence/commit/6b4fe01f4e368b521480ecf0b63538976d224210))
* **gene-profile:** cite-ranked literature digest and run information ([80156ea](https://github.com/athril/agentic-target-evidence/commit/80156eadea47d932185a0b2706e90e282d82bbef))
* **gene-profile:** each CRISPR screen's own call, side by side ([882d8d3](https://github.com/athril/agentic-target-evidence/commit/882d8d304edddac03bf7e22a5900694087fd489a))
* **gene-profile:** expression by kind of evidence ([85ee8d7](https://github.com/athril/agentic-target-evidence/commit/85ee8d766758419ec6be3d26225035eed4d62127))
* **gene-profile:** flag STRING-only binding, bare association totals and "proven" ([e674a52](https://github.com/athril/agentic-target-evidence/commit/e674a52dd65282a2853478fba10aeebbd7fbb672))
* **gene-profile:** ground the literature by identifiers and build the allelic series ([9e17726](https://github.com/athril/agentic-target-evidence/commit/9e177268637b1983711777b02dfc193656e30bf8))
* **gene-profile:** keep safety evidence apart by kind ([f49c83d](https://github.com/athril/agentic-target-evidence/commit/f49c83d2c057251211ea4eedefc13539fa233a7f))
* **gene-profile:** list the structures by resolution ([9430816](https://github.com/athril/agentic-target-evidence/commit/94308165e7926c1fe5e26238af3c3a7683b64699))
* **gene-profile:** name each ClinVar call's condition and each row's alleles ([4ab0952](https://github.com/athril/agentic-target-evidence/commit/4ab09520ec586cccc5ea81ccdef3a45582d1cc4b))
* **gene-profile:** one property per allelic-series column, with checked studies ([01f3522](https://github.com/athril/agentic-target-evidence/commit/01f3522ba69abf7f8d25be2fd33beee8f5365a1c))
* **gene-profile:** programmatic tables and figures, researcher-first checks ([818be44](https://github.com/athril/agentic-target-evidence/commit/818be44f41f273019581bab55b21dc3d4aec5f47))
* **gene-profile:** type variant effects from UniProt's notes and tally them ([c6c38c0](https://github.com/athril/agentic-target-evidence/commit/c6c38c08658e4dac99a7ff24a74d4e147001e499))
* **gene-profile:** write Markdown only by default, PDF on request ([2524876](https://github.com/athril/agentic-target-evidence/commit/2524876d6d91ede1aa33e4bb010980a361fb4694))
* **gene-profile:** write the indication from the disease-scoped records ([f6f5346](https://github.com/athril/agentic-target-evidence/commit/f6f53465cc20cf104a81f2f93e5ba472a65ae822))
* **genetics:** count ClinVar calls for the disease they name ([fed3e0e](https://github.com/athril/agentic-target-evidence/commit/fed3e0edb993fe9533193c2a6979888e99e25685))
* **genetics:** fetch Orphanet prevalence for the indication's own OrphaCodes ([7fe5474](https://github.com/athril/agentic-target-evidence/commit/7fe54745aa8ac1b2b7b0efed211024f4e50a9a63))
* **genetics:** learn each variant's effect on the target from the run's own sentences ([539d303](https://github.com/athril/agentic-target-evidence/commit/539d303f49a6d38b6422bcf0a70db3733bdfd28a))
* **genetics:** read a variant's effect on the target from data, its risk from statements ([6461cff](https://github.com/athril/agentic-target-evidence/commit/6461cff6c7efd48de0ca7ba24433fc439b9e85ad))
* **genetics:** read ClinVar itself, with gnomAD's mirror as fallback ([b80cf8b](https://github.com/athril/agentic-target-evidence/commit/b80cf8b703cdbcdc86913ec3a3cb9e218bb29b38))
* **genetics:** read literature direction from typed allele effects ([63035fd](https://github.com/athril/agentic-target-evidence/commit/63035fd3549f69001f7b7e63a678038cd039d510))
* **genetics:** read the direction of effect from GWAS–QTL colocalisation ([7393e5b](https://github.com/athril/agentic-target-evidence/commit/7393e5b32e69939c0b967159e691e35023d3a455))
* **genetics:** set causality by rule from curated records or replicated human genetics ([fa3ee7d](https://github.com/athril/agentic-target-evidence/commit/fa3ee7d300c11e7c7737c3aec33242e3ba5560e4))
* **genetics:** take the allele's gene change from the claim's typed effect, ask only for risk ([64ef2d0](https://github.com/athril/agentic-target-evidence/commit/64ef2d02e6420e68f9d31d210f929ca2f05b2ee1))
* **gnomad:** name the release and transcript the constraint was computed on ([f4c515f](https://github.com/athril/agentic-target-evidence/commit/f4c515fc34f7bb5e458fee28af521e439a1a179d))
* **guards:** check lens statements of genetic direction against the shared reading ([31014f8](https://github.com/athril/agentic-target-evidence/commit/31014f80137494f5f404a885db879f2e5fe25e54))
* **guards:** ground lens prose in the target's own data and penalise flagged axes ([748befc](https://github.com/athril/agentic-target-evidence/commit/748befcf323b6492dbd9f9a29c28a2f0d1f4c82a))
* **icite:** article citation metrics from NIH iCite ([adf6b2d](https://github.com/athril/agentic-target-evidence/commit/adf6b2d9984feadb70baeb83455cc482db583960))
* **identity:** resolve the target's NCBI Gene id and the scope's MeSH ids ([6efdef3](https://github.com/athril/agentic-target-evidence/commit/6efdef33ce3798ededfdcbadb25e255cd53d52c7))
* **lenses:** always show decisive evidence and report coverage per kind ([9e29361](https://github.com/athril/agentic-target-evidence/commit/9e2936115ac0b9c574adc673067b01bc6f6b054c))
* **lenses:** budget the fixed prompt blocks and guard the context window ([678938b](https://github.com/athril/agentic-target-evidence/commit/678938b416e507c47dc31f5c9cb3c1d62deb9655))
* **lenses:** collapse restated findings and rank claims by relevance to the lens ([5e7e282](https://github.com/athril/agentic-target-evidence/commit/5e7e282b2f9f651ed93a092bd4af7f261f5c49a5))
* **lenses:** compute confidence from cited evidence; add citation and repair steps ([e8e787c](https://github.com/athril/agentic-target-evidence/commit/e8e787ca03f21b2f83647b3c653230e0ae763ad0))
* **lenses:** decide a paper's scope with the disease scope ([5ea00d9](https://github.com/athril/agentic-target-evidence/commit/5ea00d982c0a2a5d84a60cf9a1fdbd8cf60dbdf6))
* **lenses:** flag an axis whose rationale contradicts its value ([733dba7](https://github.com/athril/agentic-target-evidence/commit/733dba72d936b525fafb4b4f5f1c7059a19ec8f4))
* **lenses:** give each evidence kind its own slots in a lens's view ([47dddc4](https://github.com/athril/agentic-target-evidence/commit/47dddc43ada3b22b70bcd511a1fdd3c36d24d0f1))
* **lenses:** rank a lens's literature by paper, study design and impact ([a1f3998](https://github.com/athril/agentic-target-evidence/commit/a1f39984c64e2516b038084b8286611b367a9bc3))
* **lenses:** route drug records by their relation to the target ([8259cff](https://github.com/athril/agentic-target-evidence/commit/8259cffb90fecc6710b67f7b3dce7bd492052e3d))
* **lenses:** run the safety lens as typed questions and weigh failed programmes by outcome ([f7a0f47](https://github.com/athril/agentic-target-evidence/commit/f7a0f472a4401c15d4173d02e38c73416ded1765))
* **lenses:** stop commercial and regulatory re-scoring the direction of effect ([88f388b](https://github.com/athril/agentic-target-evidence/commit/88f388bc30a9be07ddbcc1d0bd0022d9cac62de7))
* **lenses:** treat a secreted target's producing tissues as a site of action ([ea736cf](https://github.com/athril/agentic-target-evidence/commit/ea736cf0c8fd7cc448932ed7664734cb3bbe95c2))
* **literature:** retrieve perturbation studies in the disease's animal models ([e9e514a](https://github.com/athril/agentic-target-evidence/commit/e9e514a7ef9b9194a8c891f078485055d62ed9d8))
* **observability:** record what a run went without as state, not log lines ([c688126](https://github.com/athril/agentic-target-evidence/commit/c688126e509260138785e1298396906addd3e5b3))
* **ontology:** parents of the disease's cross-referenced terms from OLS4 and MeSH ([a75587b](https://github.com/athril/agentic-target-evidence/commit/a75587bfced85f57c92467555cf630993dec1d71))
* **orphanet:** serve each disorder's phenotypes, age of onset and inheritance ([74314b1](https://github.com/athril/agentic-target-evidence/commit/74314b11f10d29f0d0d4057840f564581170b025))
* **panel:** report designed rules apart from rescue rules ([65d1e94](https://github.com/athril/agentic-target-evidence/commit/65d1e94445542ffc3e743863919dee1c95b83636))
* **panel:** report what each lens was shown and which runs had unscored literature ([e18f11b](https://github.com/athril/agentic-target-evidence/commit/e18f11b769fb677524eb8dc4f09d2eff92c0a02f))
* **programmes:** count a surrogate win with a hard-endpoint loss as a failure ([5011b19](https://github.com/athril/agentic-target-evidence/commit/5011b19b2a69982765a33796f62d216f350e072d))
* **pubmed:** keep publication types, MeSH headings and retractions of each paper ([1e5b84e](https://github.com/athril/agentic-target-evidence/commit/1e5b84e4f5034a0cfd4e8a117793f974b186dc8a))
* **pubtator:** fetch PubTator3 entity annotations of PubMed abstracts ([bbc0c79](https://github.com/athril/agentic-target-evidence/commit/bbc0c79a14bbd303b5a9e957ad1e640a5c5a50b4))
* **pubtator:** keep the relations PubTator3 extracts from each paper ([63cc8fc](https://github.com/athril/agentic-target-evidence/commit/63cc8fcabc31b8c67e01e781db19a5b633190430))
* **quality:** give each paper its own iCite metrics beside journal quality ([8601dba](https://github.com/athril/agentic-target-evidence/commit/8601dba404c0cea658dbd3cc7209f6b2a375be95))
* **regulatory:** compute the overall verdict from the lens's own axes ([007e288](https://github.com/athril/agentic-target-evidence/commit/007e288c798c6b4b3067c8b9c561c818c799acf6))
* **report:** show lens confidence as an evidence band ([7ed5d71](https://github.com/athril/agentic-target-evidence/commit/7ed5d713a5f3a3c65798475b2887e445ba8a2bc6))
* **report:** stamp version on top and run information at the bottom ([dc49e10](https://github.com/athril/agentic-target-evidence/commit/dc49e100996a4e6b6f243604441c7e2a5c91209d))
* **retrieval:** search trials of the target's drugs in the run's indication ([3a8cc72](https://github.com/athril/agentic-target-evidence/commit/3a8cc72bf93543fefb3b5b1b575e022ab347072d))
* **review:** coverage checks, prose QA and visible synthesis failures ([604ac86](https://github.com/athril/agentic-target-evidence/commit/604ac865cd4db7c5e9422fb8203ea5e9ab688a0c))
* **routing:** register every reachable provider via a shared build_router ([50d4f87](https://github.com/athril/agentic-target-evidence/commit/50d4f87af99adf425f69a31e2f80741a2df93e9e))
* **routing:** run on qwen3.5:9b with thinking switched off ([32f266d](https://github.com/athril/agentic-target-evidence/commit/32f266d3ea71ac17a64feb88f0be8a83361ea39b))
* **safety:** build candidate liabilities from structured data ([e1efcf6](https://github.com/athril/agentic-target-evidence/commit/e1efcf6f156230a26ea0951cdf4cfede4cd56192))
* **safety:** compute the verdict from the gene's findings, with one call for the prose ([a7fa2ee](https://github.com/athril/agentic-target-evidence/commit/a7fa2eebb69dfefb5b7f8842e08bc55fb33e0f10))
* **safety:** count liabilities by the direction that causes them and report an unassessed direction ([4d28c42](https://github.com/athril/agentic-target-evidence/commit/4d28c42b01d16dfc84a4774f142e496380165104))
* **safety:** give the human-harm question the deaths and serious events trials posted ([fa55fb3](https://github.com/athril/agentic-target-evidence/commit/fa55fb3e173974dca78617cf2e409ebb3715cad8))
* **safety:** read harm in people from stop reasons and posted trial results ([0880de2](https://github.com/athril/agentic-target-evidence/commit/0880de205affd2ce5472ac84435e38bcd6e7d5b0))
* **safety:** treat human genetics as evidence of harm and of tolerance ([090394b](https://github.com/athril/agentic-target-evidence/commit/090394b6812fb1927c5a3e9bca483ade933752ae))
* **scope:** count the disease's cross-ontology parents as related text ([d1e7f2c](https://github.com/athril/agentic-target-evidence/commit/d1e7f2cee068579b5aaabd39c8b26afe793fc098))
* **score:** count the direction of effect once, never as a liability ([fea6111](https://github.com/athril/agentic-target-evidence/commit/fea61115857fdae1f137651bb7b04556299124f1))
* **score:** let lenses report precedent and count it once in the score ([8ed877b](https://github.com/athril/agentic-target-evidence/commit/8ed877ba45e7c1ce51924172c0c3d2387cc31778))
* **screening:** give the second pass a sectioned excerpt instead of the opening of the paper ([0d26d4b](https://github.com/athril/agentic-target-evidence/commit/0d26d4bda5b2bf9aa593179566a7fac613f07df7))
* **scripts:** check the network and proxy setup before a panel run ([cce2644](https://github.com/athril/agentic-target-evidence/commit/cce264480d363df40bf330f747562514ece5da06))
* **skills:** rename gene-dossier to gene-profile ([0fa657d](https://github.com/athril/agentic-target-evidence/commit/0fa657dadd542a338ec957a1d6f3ede93b07b4ab))
* **skills:** ship gene-dossier as a Claude Code plugin ([90a927e](https://github.com/athril/agentic-target-evidence/commit/90a927eef3f341f4f4766f0f4e861816878c274e))
* **statements:** cap the sentences read per run, not only per paper ([c85f379](https://github.com/athril/agentic-target-evidence/commit/c85f3795dbcd61e782f5c1582d01c41131801ce6))
* **tissue:** infer disease-relevant tissues from the disease name and therapeutic area ([05cad30](https://github.com/athril/agentic-target-evidence/commit/05cad300667ed39140ecc4ef20ec1ec33ad43163))
* **tokens:** count prompt tokens with the model's measured ratio ([570b8fe](https://github.com/athril/agentic-target-evidence/commit/570b8fefde6a28d84148f98b61b018daccec2d69))
* **trials:** attribute registry-only oligonucleotide programmes to the target ([6320374](https://github.com/athril/agentic-target-evidence/commit/632037465847fe9ce76045e401d4a9c7a499102d))
* **trials:** drug-name trial expansion, posted results and trial attribution ([e02ce6f](https://github.com/athril/agentic-target-evidence/commit/e02ce6f866712528d5cb681b4c1cdead3b4f613e))
* **trials:** link outcome trials that measured the indication to their programmes ([b1246e5](https://github.com/athril/agentic-target-evidence/commit/b1246e55923b7a2e6d5dd180fc92fa948cfc1ca0))
* **workflow:** wire liabilities, scoped evidence and trial facts into the graph ([9efef51](https://github.com/athril/agentic-target-evidence/commit/9efef5105d4e6a930cac134925b3211562cfb49d))


### Bug Fixes

* **biology:** leave essentiality to the safety lens and set the verdict from the axes ([f365800](https://github.com/athril/agentic-target-evidence/commit/f365800572d3db16307f66a811e7f89387f69b9a))
* **cache:** key lens verdicts on a digest of their inputs ([cf362b6](https://github.com/athril/agentic-target-evidence/commit/cf362b6c41f47db1ee089cdea110ac6e92190df2))
* **cache:** key source-quality and lens caches on each step's model ([49c071c](https://github.com/athril/agentic-target-evidence/commit/49c071cf8a8bee4346ad9cad01a197f84510aabe))
* **cache:** skip stale evidence rows instead of discarding the whole hit ([43f021f](https://github.com/athril/agentic-target-evidence/commit/43f021f2b21d411654adbb7db5c8099b5ec1ed2a))
* **cache:** treat stale cached lens verdicts as a cache miss ([57226ac](https://github.com/athril/agentic-target-evidence/commit/57226ac93f2149af42975161ecccb531ce9a8f95))
* **checkpoint:** run the checkpointer on a validated connection pool ([8cca0dd](https://github.com/athril/agentic-target-evidence/commit/8cca0dd1760d4d568dfa71bec8937f5c9be21436))
* **chembl:** report the bioactivity count as a floor when its query fails ([cb41ea1](https://github.com/athril/agentic-target-evidence/commit/cb41ea1613bfb0ac05d454baf3d3ebb3257bd504))
* **claims:** ground extracted claims in their own source ([2a930cd](https://github.com/athril/agentic-target-evidence/commit/2a930cdd4fbb1df47e621fb7a69f6591f0d24858))
* **clinical:** list trials in the indication apart from the drugs' other trials ([5da38f3](https://github.com/athril/agentic-target-evidence/commit/5da38f3a6032e74e8f7103a9b88a5fc099a11d79))
* **clinical:** name each drug under the stage it reached and arm the approval guard ([da015a9](https://github.com/athril/agentic-target-evidence/commit/da015a963da48dc98bdaf88cd6d6e9792936e2d0))
* **clinicaltrials:** retry rate-limited results fetches within the request limit ([3be681b](https://github.com/athril/agentic-target-evidence/commit/3be681b1d12bcd412a82431021cbf5825b2d6388))
* **cli:** point make run and docs at src/run_analysis.py ([12a62ae](https://github.com/athril/agentic-target-evidence/commit/12a62aed87b57caa9b11fe6b1b13904fc6030409))
* **commercial:** compute the competitive axis and verdict from the programmes ([e4e3b92](https://github.com/athril/agentic-target-evidence/commit/e4e3b92114e0e8266cea60aa7850a78229413060))
* **commercial:** count only drugs acting in the requested direction as competitors ([d2c6fb3](https://github.com/athril/agentic-target-evidence/commit/d2c6fb33574db0ba73b376dcc5b2209c143adbc5))
* **commercial:** flag patent encumbrance asserted from patent titles ([fbd1ec7](https://github.com/athril/agentic-target-evidence/commit/fbd1ec73ea3030e345658dc10d9ef412210c26d9))
* **commercial:** judge competitive opportunity at the target level, not by indication crowding ([4dc9473](https://github.com/athril/agentic-target-evidence/commit/4dc9473f599a5bb02cd72cec651c1495e501192a))
* **commercial:** tell the lens the indication is contested, not underserved ([96d9103](https://github.com/athril/agentic-target-evidence/commit/96d9103f6a7dd6cab5910c948c538ae2f16340d1))
* **competition:** search the disease's other names when its own matches no label ([df69f12](https://github.com/athril/agentic-target-evidence/commit/df69f12753395df8a1432b07b9e796e3e1d5c9f3))
* **confidence:** a corroborating axis discounts a lens but never caps it ([3a663c9](https://github.com/athril/agentic-target-evidence/commit/3a663c9eee27c0a1f4c4c267c0341fc98e504c4f))
* **config:** map Open Targets' current therapeutic-area ids to disease classes ([7292113](https://github.com/athril/agentic-target-evidence/commit/7292113fd796d501d2f53b452a2d8b6e79502a6e))
* **constraint:** read constraint as population depletion ([110841d](https://github.com/athril/agentic-target-evidence/commit/110841dd49be65f0e9f947147b7980d5ddc97313))
* **depmap:** fall back to the figshare release when the portal returns a bot-check page ([eca0929](https://github.com/athril/agentic-target-evidence/commit/eca0929b60a1a0b20837d57a16bbe8d48d62098d))
* **deps:** upgrade 17 packages to clear 85 dependabot alerts ([b4698cf](https://github.com/athril/agentic-target-evidence/commit/b4698cf85c69e9b48ce20d9e804eeb64b8fcf6b7))
* **direction:** count a label warning only when it governs the indication ([1ce4aab](https://github.com/athril/agentic-target-evidence/commit/1ce4aab7d90e1f0aea82311e0d89c10493145575))
* **direction:** read label warnings with the disease scope ([7adfd51](https://github.com/athril/agentic-target-evidence/commit/7adfd518ae14afd4f9a5b308810acc74a3975794))
* **direction:** scope literature direction claims with the disease scope ([23aac1f](https://github.com/athril/agentic-target-evidence/commit/23aac1ff62067bb9a34ccd427f3e627c9e1bd0fc))
* **direction:** scope literature direction evidence to the target and disease, and net out its noise ([1d3b921](https://github.com/athril/agentic-target-evidence/commit/1d3b921508a7478cb2b23aa1c9d1d14ca7d1ef0b))
* **expression-atlas:** raise the client timeout to 120 s ([3bdd6f3](https://github.com/athril/agentic-target-evidence/commit/3bdd6f34ebabd664653c66da01d1a9c88a5f754c))
* **extraction:** extract claim directions without showing the run's direction ([0f2433b](https://github.com/athril/agentic-target-evidence/commit/0f2433b2f986be9542f9754d4556792b2ab4f8c3))
* **extraction:** record faithfulness verdicts as flags and stop counting literature harms ([d6c64f2](https://github.com/athril/agentic-target-evidence/commit/d6c64f2ca20fa5c00a1d277767e1ba9637e6e9c5))
* **faers:** report serious and fatal outcomes as shares of reports ([c53770e](https://github.com/athril/agentic-target-evidence/commit/c53770eb1efba2449005d04edc203afc5dff89cd))
* **gene-profile:** an anchor fails when its format or unit does not apply ([d039cbd](https://github.com/athril/agentic-target-evidence/commit/d039cbdda5807cc90ff1c39b43a54058589bdb72))
* **gene-profile:** drop the sections render.py writes from the draft ([be9098c](https://github.com/athril/agentic-target-evidence/commit/be9098c6ca33bf396efdc989782cf7ce9dca1790))
* **gene-profile:** link exact records and requests, never blank searches ([9333e94](https://github.com/athril/agentic-target-evidence/commit/9333e9465badbaf2a86d7d5e13a91dab530a03bd))
* **gene-profile:** report licence-gated GBD as not checked ([732519a](https://github.com/athril/agentic-target-evidence/commit/732519a04dcc1d773c6d40252c220360e9f866eb))
* **genetics:** compute the genetics verdict from its axes ([ab07042](https://github.com/athril/agentic-target-evidence/commit/ab0704234e46c5b50d01fde3835af9ffc961a286))
* **genetics:** read a related disorder's Mendelian mechanism as its own, not the indication's ([539b557](https://github.com/athril/agentic-target-evidence/commit/539b557351fce633880b6c106bf6d9ace8292cb2))
* **genetics:** read homozygous loss-of-function carriers into the constraint reading ([012e435](https://github.com/athril/agentic-target-evidence/commit/012e4356b4c6e12b25a2799a1bd1e33dae7e56c3))
* **genetics:** refetch cached genetics evidence for the indication's Orphanet prevalence ([27e4a50](https://github.com/athril/agentic-target-evidence/commit/27e4a503bcafa53f51d7e65d77edcd3d0898af01))
* **genetics:** refetch cached genetics retrieved before the full GWAS scan ([058b9bb](https://github.com/athril/agentic-target-evidence/commit/058b9bbe46e7df288193d03dbf8892dfd0f9ee17))
* **genetics:** scope Mendelian evidence to the run's disease ([c8940c0](https://github.com/athril/agentic-target-evidence/commit/c8940c044d7b3999324a4ec83adace621d147e09))
* **genetics:** steer GoF genes away from the "missense-intolerant" trope ([03eb9b2](https://github.com/athril/agentic-target-evidence/commit/03eb9b2c1a06f978a70a10e39325b358b927933a))
* **genetics:** stop a cached Open Targets row from skipping the genetics agent ([2a3eb2b](https://github.com/athril/agentic-target-evidence/commit/2a3eb2bf105fdf546bd34d0a2b38e8087c632f67))
* **genetics:** support only when the direction of effect is not contradicted ([f7742f5](https://github.com/athril/agentic-target-evidence/commit/f7742f58f0d84cc17cd329210412c4d1d0eea54f))
* **genetics:** type variants by their consequence and never read a splice variant's QTL ([7fc820e](https://github.com/athril/agentic-target-evidence/commit/7fc820e3620b8c2090e5f5c583f9f592181b13c1))
* **grounding:** place a disease mention in scope by its names when its MeSH id is not ([f55654e](https://github.com/athril/agentic-target-evidence/commit/f55654ed3b78d1565ad1c516b32c606105f2a15b))
* **grounding:** take a variant's carried allele from its own notation ([48e2ccb](https://github.com/athril/agentic-target-evidence/commit/48e2ccb0b5922a3db961c7110799145145e67333))
* **guards:** flag a denied boxed warning only for a drug acting on the target ([a0792b7](https://github.com/athril/agentic-target-evidence/commit/a0792b754a2fa9c8ab3aa84d96e4be4d547f7db5))
* **guards:** flag figures from sources the run never had and non-oncology slips ([f15f9a5](https://github.com/athril/agentic-target-evidence/commit/f15f9a58af4c5a05c3492b6220bf564f7c57d46d))
* **guards:** ground a figure quoted from a table headed by its source ([443d6ad](https://github.com/athril/agentic-target-evidence/commit/443d6ad7bda839fad3b02b09f680502d88a76395))
* **guards:** read powers of ten and trial phases as no source figure ([7bf7e1d](https://github.com/athril/agentic-target-evidence/commit/7bf7e1d01013b3a593cdc8a20c3a5d8c4a551b23))
* **guards:** say why the phenotype-omission check did not run ([58955aa](https://github.com/athril/agentic-target-evidence/commit/58955aad98f461f38d7a32c3f075b3719c083eaa))
* **guards:** scope the off-target boxed-warning check to the drug named ([c64c0d5](https://github.com/athril/agentic-target-evidence/commit/c64c0d58457d6108807978c71f5e9d6e8c118b43))
* **guards:** skip haploinsufficiency mentions the sentence dismisses ([2eba077](https://github.com/athril/agentic-target-evidence/commit/2eba077b2b479fafc8862e35d2dcfe9499ff35e5))
* **gwas:** scan every significant association when the query is scoped to a disease ([6f39788](https://github.com/athril/agentic-target-evidence/commit/6f397888c308a03c8c6944c9f29934989ca8ad43))
* **http:** honour Retry-After and rate limits in the shared client and connectors ([c829353](https://github.com/athril/agentic-target-evidence/commit/c8293530e89a603543cb9ac4cc15a9962687e34c))
* **http:** retry a request that failed to connect ([2bc708c](https://github.com/athril/agentic-target-evidence/commit/2bc708c42d6e03cc932b024aed8f60ac67cb7325))
* **icite:** send at most 200 PMIDs per request ([c358aab](https://github.com/athril/agentic-target-evidence/commit/c358aab3bfb59c2d4d374a20b9bc3fa797fe5889))
* **impc:** match the mouse symbol so knockout phenotypes are found ([4b75bc7](https://github.com/athril/agentic-target-evidence/commit/4b75bc709e1801839bcfd32b5e18455d10cd03d3))
* **investigator:** reach a local MCP gateway without the environment's proxy ([2dbb54b](https://github.com/athril/agentic-target-evidence/commit/2dbb54bfcb6c01b3ba1ee460065031a5833ac95a))
* **lenses:** a tested-in-the-clinic axis never counts against an oppose ([c48beeb](https://github.com/athril/agentic-target-evidence/commit/c48beebfa2f8bd6a51a148a005a638d3af394812))
* **lenses:** align prose that states a verdict the rules then changed ([2e17bb0](https://github.com/athril/agentic-target-evidence/commit/2e17bb0e3a04365a22498169115260c2c75adc85))
* **lenses:** apply the biology, safety, genetics and commercial rules in code ([9f908fe](https://github.com/athril/agentic-target-evidence/commit/9f908feff086c1c9f7a82fc2714d5784c9265f2d))
* **lenses:** ask once for valid JSON when a lens verdict cannot be parsed ([f28bcdd](https://github.com/athril/agentic-target-evidence/commit/f28bcdd057a77012e2eaede92ec84ecbb9af31e5))
* **lenses:** bound the reply length of a question answered once per claim ([e243d04](https://github.com/athril/agentic-target-evidence/commit/e243d048c4afe0951ecc610e8d8cf2c22fa85faa))
* **lenses:** calibrate strength for direction opposes and unsettled safety support ([70b1c7b](https://github.com/athril/agentic-target-evidence/commit/70b1c7b7ac5ea3fd3f1a6da516040782d4baeb96))
* **lenses:** cut model deliberation leaked into verdict prose ([700ac2e](https://github.com/athril/agentic-target-evidence/commit/700ac2eb10f34dec81574e4c534ec43e8e779bbc))
* **lenses:** de-specialise lens prompts and tighten per-lens rules ([f55d691](https://github.com/athril/agentic-target-evidence/commit/f55d6917690925b44802b86fed9ff87a3c461b7c))
* **lenses:** fit lens prompts into the model's context window ([470ea51](https://github.com/athril/agentic-target-evidence/commit/470ea51e2714903c7c6379897c42bbcd15510e2a))
* **lenses:** key cached lens verdicts on the version of the lens rules ([a842a9f](https://github.com/athril/agentic-target-evidence/commit/a842a9f37fc9a023d38540082844c1e3acdce92f))
* **lenses:** make output guards clause-aware and report unevaluated checks ([d6292ce](https://github.com/athril/agentic-target-evidence/commit/d6292ceac8d0e482e5615277fc6c9a5ccbd1a88a))
* **lenses:** record a corrected sentence whole, not cut at 300 characters ([dbb8181](https://github.com/athril/agentic-target-evidence/commit/dbb8181cfec784ef5508ef2b4a3d761461620944))
* **lenses:** refresh what a verdict derived when a rule replaces it ([053a119](https://github.com/athril/agentic-target-evidence/commit/053a11913aad076533b159e99369b6517ed96a88))
* **lenses:** show a lens the most informative trials first ([279da23](https://github.com/athril/agentic-target-evidence/commit/279da23be1d3558d3262f80469360fe818850bbe))
* **lenses:** stop relevance and quantity guards flagging compliant prose ([b94a03f](https://github.com/athril/agentic-target-evidence/commit/b94a03f365bf4c241ef38cbd865e5f929dd88adf))
* **lenses:** treat a secreted target's producing tissues as where it acts ([f9dd2ed](https://github.com/athril/agentic-target-evidence/commit/f9dd2edb99f58a50a2b7c8116787c541e328d2cb))
* **links:** point Project Score and TTD at real records, label by source ([06dddde](https://github.com/athril/agentic-target-evidence/commit/06dddde8ae94e12e566038028a78f4b13ab5d97b))
* **literature:** cache a disease with no model phenotype, never a failed lookup ([3494091](https://github.com/athril/agentic-target-evidence/commit/3494091d43ec151838ae470ead64eccbf1dcdc4d))
* **literature:** refetch cached rows that lack PubMed's registry ids ([7fe816e](https://github.com/athril/agentic-target-evidence/commit/7fe816e7eec71978bd176c681c37928c7d670137))
* **literature:** retrieve the indication's trials of any of the target's drugs, at any date ([f226ea6](https://github.com/athril/agentic-target-evidence/commit/f226ea69bbef6f4a4f49a466ad9751dcb2396672))
* **llm:** recover common JSON slips and seed Ollama sampling ([142b03c](https://github.com/athril/agentic-target-evidence/commit/142b03c4e75a87a4b94a04bcaae61ea885ed1c82))
* **observability:** tell a clean run from one that predates the degradation ledger ([9c698f0](https://github.com/athril/agentic-target-evidence/commit/9c698f07e7265022e3628e70ef4810f687dd22a4))
* **ontology:** request OLS parents over https instead of following the returned link ([3390b2f](https://github.com/athril/agentic-target-evidence/commit/3390b2fbc7de3789bb7ae8751f0bb692b91fadd9))
* **openfda:** fetch the target's drug labels by name and keep only their liabilities ([cd95cdf](https://github.com/athril/agentic-target-evidence/commit/cd95cdfca11282ed87d8633a1c94e5e5f9041e77))
* **openfda:** name the labels openFDA has not harmonised from their own text ([37e108a](https://github.com/athril/agentic-target-evidence/commit/37e108abbfb6430e7098727069b1b17c7d69f393))
* **openfda:** search labels by the disease's synonyms and drop diagnostic labels ([7feb4ce](https://github.com/athril/agentic-target-evidence/commit/7feb4ce16c2e16a822eb325d72eaf1edbb9fc2b6))
* **opentargets:** give each known-drug indication its own stage, approval and failed trials ([bdd53d4](https://github.com/athril/agentic-target-evidence/commit/bdd53d4d5bbb10b2e176a7ad6ba2cf25c2c277f9))
* **panel:** read DATABASE_URL from .env when scoring a panel ([b8a6139](https://github.com/athril/agentic-target-evidence/commit/b8a6139145d90beb8fa224a0ebd504e99301b627))
* **panel:** report the lenses' own signal beside the gated band ([122ac84](https://github.com/athril/agentic-target-evidence/commit/122ac84e7955da5a952d0212c9d4dfa4db12dc93))
* **persistence:** chunk evidence upserts under the driver's parameter limit ([5f60dc4](https://github.com/athril/agentic-target-evidence/commit/5f60dc45bc6d1a50b863c69428d049ab263ab1fe))
* **precedent:** an approval in the indication relieves the falsification of its mechanism ([f639412](https://github.com/athril/agentic-target-evidence/commit/f6394129f9e6bb8281b341a175f9c3f75f947a1e))
* **precedent:** lift axes that already agree with a decisive precedent to its confidence ([4037d7d](https://github.com/athril/agentic-target-evidence/commit/4037d7d6f16d24dd67006bdac21d44b497b450e6))
* **programmes:** a met primary that is itself a hard endpoint is no surrogate win ([5d497e5](https://github.com/athril/agentic-target-evidence/commit/5d497e5606d01cd0a85abeb89ef308660df2751c))
* **programmes:** a met primary whose secondaries were never read is no positive ([d35efe8](https://github.com/athril/agentic-target-evidence/commit/d35efe8ff88e8baf6537888e4ae72db985bb1f1c))
* **programmes:** a trial stopped early is not a positive one ([9eef3d7](https://github.com/athril/agentic-target-evidence/commit/9eef3d74df8adf5bd2155ab58cf09dd37d7d4bb4))
* **pubmed:** a malformed full-text record costs that record only ([5a68c75](https://github.com/athril/agentic-target-evidence/commit/5a68c756270d8c89b4743450221935f479d15970))
* **pubmed:** resolve PMC ids from the pubmed_pmc elink so full text is fetched ([5bcbfc8](https://github.com/athril/agentic-target-evidence/commit/5bcbfc8c345475a6aafc55b30ed53c5f3a7f004b))
* **pubtator:** split a rejected batch so one bad PMID costs only itself ([0b26042](https://github.com/athril/agentic-target-evidence/commit/0b2604270acecf3b01ec61aadaac6033a4153a31))
* **quality:** reuse cached source quality on a repeat run of the same pair ([387a142](https://github.com/athril/agentic-target-evidence/commit/387a1422a851a265c7d6281a981f94e51cf98190))
* **regulatory:** say neutral, not insufficient, when label data exist ([5a0bbe1](https://github.com/athril/agentic-target-evidence/commit/5a0bbe1bdc8767bb7f723417ef122e118e51b3aa))
* **regulatory:** state each label's direction and any warning about the indication ([58671cf](https://github.com/athril/agentic-target-evidence/commit/58671cfe20639abe333eec74ce3cb835c6fa1b8c))
* **repair:** let a repair delete or soften a sentence, never add a fact ([a58f774](https://github.com/athril/agentic-target-evidence/commit/a58f77471350e47fe1da3ecc932ed73960b9c572))
* **repair:** reject a rewrite that brings in a missing-value placeholder ([401c18b](https://github.com/athril/agentic-target-evidence/commit/401c18b2ba3da2d6a11cabdc8c8f8b546d0935e4))
* **report:** list cached evidence in the full report and summary CSV ([69586de](https://github.com/athril/agentic-target-evidence/commit/69586de6734aa799a0bf8bb89fa964a32d97e7c8))
* **report:** name each finding's source and drop a placeholder model confidence ([e02a575](https://github.com/athril/agentic-target-evidence/commit/e02a57556d3941c7b1a487bf3f087de8acb4b316))
* **report:** print each guard note once instead of in every summary copy ([b0977af](https://github.com/athril/agentic-target-evidence/commit/b0977af381e7dc555ad39541adb2d9b3af327035))
* **retrieval:** add target-level literature queries and re-screen unanswered items ([71c75a5](https://github.com/athril/agentic-target-evidence/commit/71c75a50ac22d2612c9043a334421cae1e4001b9))
* **routing:** honour task-keyed agent_models entries ([dc49e10](https://github.com/athril/agentic-target-evidence/commit/dc49e100996a4e6b6f243604441c7e2a5c91209d))
* **safety:** a favourable toxicity axis reached by absence counts as a non-finding ([63159eb](https://github.com/athril/agentic-target-evidence/commit/63159eb855734a69874240829644f22f05a20707))
* **safety:** apply the verdict rules before the split lens writes its narrative ([8ce3981](https://github.com/athril/agentic-target-evidence/commit/8ce398194762d94611347f874c417962547b1f9c))
* **safety:** count terminated phase 2+ trials as exposure and worsening on two compounds as harm ([1c2675e](https://github.com/athril/agentic-target-evidence/commit/1c2675e4e078902eb3248e49aea5ff466eeb31ff))
* **safety:** rate death under an experimental challenge as a medium liability ([67a2f6f](https://github.com/athril/agentic-target-evidence/commit/67a2f6f984c9a272e42d733d5a92fa2db04c73c9))
* **safety:** read curated events' direction and count them once per change ([c2ac031](https://github.com/athril/agentic-target-evidence/commit/c2ac031be20d3b591d6e616a4abb4c2452615ef3))
* **safety:** round constraint metrics in the liability evidence ([b053abe](https://github.com/athril/agentic-target-evidence/commit/b053abe4e01e11282c8296e78ef311ab5630e854))
* **safety:** settle findings against every approved label, combinations included ([566a9b2](https://github.com/athril/agentic-target-evidence/commit/566a9b2a3b8ffe7afb23890f453420d5b380e620))
* **safety:** settle liabilities against the labels of drugs approved in the indication ([54a2f59](https://github.com/athril/agentic-target-evidence/commit/54a2f59844e68a4da4e779a3933af04c083c2cae))
* **score:** subtract only liabilities the safety lens left open ([7cda526](https://github.com/athril/agentic-target-evidence/commit/7cda5268c4e9b1403bd51ef888fd62d58f3e319c))
* **scoring:** gate the score on the direction of effect and lead with the decision ([654b52b](https://github.com/athril/agentic-target-evidence/commit/654b52bef7d507a0f93e483d5b69c97b0394cc25))
* **screening:** keep a trial of the target's known drug by rule, past the screening cache ([a0b80d6](https://github.com/athril/agentic-target-evidence/commit/a0b80d63e6fa45060fd4b7b16373fcdf714c4cbd))
* **screening:** keep phased drug trials that name the target gene ([b1d4e5e](https://github.com/athril/agentic-target-evidence/commit/b1d4e5ef44fcb61560d8a0ec9ed0642d8ca0b4e7))
* **screening:** keep the verdicts of a truncated reply and size it for the model ([2994b3a](https://github.com/athril/agentic-target-evidence/commit/2994b3ad776d5bc7a87076c4c2a4f3f681030264))
* **screening:** let openFDA labels and FAERS reach the lenses ([790fefe](https://github.com/athril/agentic-target-evidence/commit/790fefe683091c5239a39eab2f43c0730ac9df11))
* **screening:** let the second pass re-judge uncertain items on full text ([c474ddd](https://github.com/athril/agentic-target-evidence/commit/c474ddd2a4c3ce821be25f39e26b4026bde6104e))
* **scripts:** check only https in the network preflight ([75b494e](https://github.com/athril/agentic-target-evidence/commit/75b494e8ebbb565badb781bc977a77dd3c6f2cee))
* **statements:** a reported null result has no outcome direction ([73ed935](https://github.com/athril/agentic-target-evidence/commit/73ed93528e8de51a3a55ca4385e9f7fb8eabe694))
* **statements:** give no outcome direction to a sentence that states none ([86c9719](https://github.com/athril/agentic-target-evidence/commit/86c9719f6b0f3f5e78b94b4b44710e179842db35))
* **tokens:** learn the characters-per-token ratio per task ([8ccb7ea](https://github.com/athril/agentic-target-evidence/commit/8ccb7ea950ab309edca7ab88ebf925fd641fcfb2))
* **trials:** read a registry condition written as an inverted MeSH heading ([2e2fdc5](https://github.com/athril/agentic-target-evidence/commit/2e2fdc5ea05f586ae9495c1c123f80fc23eabb6a))


### Performance Improvements

* **genetics:** ask the allele question about three batches, indication first ([ba7e471](https://github.com/athril/agentic-target-evidence/commit/ba7e47101f9a150e9ecf44c2f43ee7bae4716586))


### Documentation

* **components:** describe build_router provider registration ([38590a9](https://github.com/athril/agentic-target-evidence/commit/38590a918c85ddb77fc61a4d1512c1d6d87e576c))
* describe the pipeline without gap detection or a replan loop ([bbad0f2](https://github.com/athril/agentic-target-evidence/commit/bbad0f2915a864056f9c722114494c210a648393))
* document gene-profile plugin and recent pipeline fixes ([2941e1c](https://github.com/athril/agentic-target-evidence/commit/2941e1c48290e5dd82ebbe7429b78a7ddd9f04c9))
* **examples:** refresh the example dossier from a current TRPC6 run ([e001945](https://github.com/athril/agentic-target-evidence/commit/e001945257af10a2a41bedf1a35cb6ca649ddf63))
* **gene-profile:** researcher-first template, writing rules and evals ([43f4670](https://github.com/athril/agentic-target-evidence/commit/43f4670c00f4b7fb561d0078476f1e16a9e80327))
* **lenses:** describe how the genetics verdict follows the direction reading ([ab32feb](https://github.com/athril/agentic-target-evidence/commit/ab32feb0360968f59f3d2e3fe15911ea9ac2be88))
* **lenses:** explain how lens confidence is computed and checked ([f6cd032](https://github.com/athril/agentic-target-evidence/commit/f6cd03242e963f7fa745c3dc3c53d0ad120263f2))
* **notice:** credit iCite citation metrics ([73d38c6](https://github.com/athril/agentic-target-evidence/commit/73d38c697394ee6912a3d1cceb1c795c622e70b7))
* **report:** align provenance docstring with explicit-assignment routing ([95f6e35](https://github.com/athril/agentic-target-evidence/commit/95f6e35cdde4af16aee908fbba7b5d0dd862aa76))

## [0.1.4](https://github.com/athril/agentic-target-evidence/compare/v0.1.3...v0.1.4) (2026-10-05)


### Features

* **skills:** add gene-dossier Claude Code skill ([8683949](https://github.com/athril/agentic-target-evidence/commit/8683949f53a8e0bf576b707bedf55238ea6b861a))


### Bug Fixes

* **commercial-lens:** separate target IP from indication-only patents ([119d664](https://github.com/athril/agentic-target-evidence/commit/119d664eefd8b784848264f2dc7fba76ce1c0925))
* **deps:** upgrade 14 packages to clear 47 dependabot alerts ([b7acc8b](https://github.com/athril/agentic-target-evidence/commit/b7acc8b4230d79363408380669f7719de3040714))
* **gwas-catalog:** migrate to REST API v2; v1 rate-limits nearly every request ([657c6ef](https://github.com/athril/agentic-target-evidence/commit/657c6ef22da3feac80a58780ed6809aa25067a26))
* **omics:** resolve GTEx and Expression Atlas by Ensembl ID; surface Atlas outages ([b6b36a7](https://github.com/athril/agentic-target-evidence/commit/b6b36a7576b023cd4c2438176b4dda802e7d5847))
* **opentargets:** count drugs at maximumClinicalStage "APPROVAL" as approved ([208792d](https://github.com/athril/agentic-target-evidence/commit/208792d45fb11e479cbc2888a58aef4096915a48))
* **project-score:** count fitness lines as scaled BF &lt; 0, not &gt; 0 ([cbbbc84](https://github.com/athril/agentic-target-evidence/commit/cbbbc848147235df385488c95d9db0e8ee846ef3))
* **pubmed:** keep full abstract text when it contains inline markup ([3d5beaa](https://github.com/athril/agentic-target-evidence/commit/3d5beaa4aed04d91da2b795967206f27de777fb5))

## [0.1.3](https://github.com/athril/agentic-target-evidence/compare/v0.1.2...v0.1.3) (2026-10-05)


### Features

* list mcp-gateway in the official MCP Registry ([7bc0829](https://github.com/athril/agentic-target-evidence/commit/7bc0829346f3266a6a23d91675ef9093aa802d9a))


### Bug Fixes

* backfill checkout fetches tags; allowlist doc placeholder in Gitleaks ([679d409](https://github.com/athril/agentic-target-evidence/commit/679d409f4e0d42764046928af8f06ced0aa4d26d))
* gitleaks allowlist must be a single table, not array-of-tables ([da24446](https://github.com/athril/agentic-target-evidence/commit/da2444630e28ed1ad0cc5260901c25783fb5e49a))
* gitleaks global allowlist key is plural [[allowlists]], not [allowlist] ([cfd50c5](https://github.com/athril/agentic-target-evidence/commit/cfd50c514dcdb105b058cd8cc0497ce84a7ffacf))
* give mcp-gateway a dedicated Dockerfile stage so it runs standalone ([61c93ef](https://github.com/athril/agentic-target-evidence/commit/61c93ef67681c86b1ee522f47e7a8492caf4b833))
* give mcp-gateway a dedicated Dockerfile stage so it runs standalone ([514253e](https://github.com/athril/agentic-target-evidence/commit/514253e9e651c248c35fa61a167dde7f4dc3328a))


### Documentation

* add example TRPC6 target validation report ([6b76b56](https://github.com/athril/agentic-target-evidence/commit/6b76b56d5ca462c4ca8692ef8c5214663eeef000))
* add GHCR image badges and pre-built image pull instructions ([8a4ebbe](https://github.com/athril/agentic-target-evidence/commit/8a4ebbe7356838c180fdc6eb0c9db1ee2a205428))
* example dossier - other files ([3cf71b3](https://github.com/athril/agentic-target-evidence/commit/3cf71b38144e6ee1845be064619b1151abb2ffc3))
* link example TRPC6 dossier from README intro ([8aa71ed](https://github.com/athril/agentic-target-evidence/commit/8aa71ed5e6c7ff432e41732d51a3090bd9e4a730))
* README.md update ([a3fa94f](https://github.com/athril/agentic-target-evidence/commit/a3fa94f380c69c1fe395d350f8f4d5bd04da867d))

## [0.1.2](https://github.com/athril/agentic-target-evidence/compare/v0.1.1...v0.1.2) (2026-06-24)


### Features

* **commercial-lens:** cite real indication-level counts in whitespace guards ([d55c2e7](https://github.com/athril/agentic-target-evidence/commit/d55c2e7e2606092772a838d8424a08b0b07e60a0))
* **mcp:** add target-agnostic indication/condition trial count tools ([b96e7b9](https://github.com/athril/agentic-target-evidence/commit/b96e7b97c189fce92556dd7d1a95e97a0b446db3))
* **schemas:** add COMPETITION evidence type and retrieval service ([3419dd3](https://github.com/athril/agentic-target-evidence/commit/3419dd371350d12686f7e0ec4cbf66bc17f74cd9))
* **workflow:** wire indication_competition into the target-validation graph ([081c45e](https://github.com/athril/agentic-target-evidence/commit/081c45e1f589c75c0fd7331529060dc5ce6957f6))


### Bug Fixes

* build GenCC source links from gene's HGNC ID ([e3c8ecb](https://github.com/athril/agentic-target-evidence/commit/e3c8ecb476fee921a93c1e40b76aa1eb37e5ace2))
* link Orphanet evidence to disease detail page not bulk-download portal ([e9c1dc0](https://github.com/athril/agentic-target-evidence/commit/e9c1dc0f96ad0ddd446b53f94bd193aba8b5085b))
* persist claim_text/source_evidence_id on evidence rows ([e334ba7](https://github.com/athril/agentic-target-evidence/commit/e334ba74f0fbfe8d9e3070dd4188f78933fabe5c))


### Documentation

* add public documentation set, contribution guides, and licensing/empowerment notices ([d94a75b](https://github.com/athril/agentic-target-evidence/commit/d94a75b60d5691debc2178076f989dbd0c83eb8b))
* link docs/README.md as a doc-set entry point from the root README ([af9e6fa](https://github.com/athril/agentic-target-evidence/commit/af9e6fa9a3b4b22e85ce74d48a8de7f7bfc8b293))

## v0.1.1 (2026-06-24)

### Bug Fixes

- Link gene ontology evidence to Monarch entity page not API host
  ([`628e849`](https://github.com/athril/agentic-target-evidence/commit/628e84937674d754747e8e8ac02b6d3d5e8f2356))

The genetics agent emitted https://api.monarchinitiative.org as the source link for gene ontology
  evidence; that bare host redirects to the Swagger docs at /v3/docs, which surfaced in reports.
  Link to the human-readable Monarch entity page (https://monarchinitiative.org/{hgnc_id}) when the
  HGNC id resolves, falling back to the Monarch homepage otherwise.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>


## v0.1.0 (2026-06-23)

### Bug Fixes

- Resolve all mypy strict-mode errors across src/
  ([`f41295b`](https://github.com/athril/agentic-target-evidence/commit/f41295b01c3b9b228513f89ab91fbc6ead8390db))

Adds missing generic type arguments, explicit return/parameter annotations, and narrows union types
  (RunnableConfig, BaseMessage subtypes, StateSnapshot.values) so `uv run mypy src/` passes cleanly
  under strict mode. Also fixes a genuine BedrockProvider call missing required model/region kwargs
  in core/a2a/run_service.py.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Stop source_quality test from depending on gitignored scimago data
  ([`412b583`](https://github.com/athril/agentic-target-evidence/commit/412b5830e592cc8ebc6e49dd5ebe8aff45d56f91))

The bundled SJR index is non-commercial-licensed and gitignored, so it never exists in CI. The test
  enabled SCIMAGO_SJR_ENABLED and relied on the real file to resolve "The Lancet" deterministically;
  when the file is missing, resolution falls through to the LLM path with an unconfigured AsyncMock,
  crashing strip_json_fence on a coroutine. Mock resolve_sjr directly instead, matching the sibling
  test's pattern.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **a2a**: Correct mTLS CA env var name and disable TLS hostname check
  ([`79c3616`](https://github.com/athril/agentic-target-evidence/commit/79c3616d91c590a6869ee1f23b8219a23fb50a0a))

_build_ssl_context() read AGENT_CA_PATH but every other config (and .env.example) defines
  CA_CERT_PATH, so the CA was silently never loaded. All services share one cert (CN=gtv-service)
  regardless of their docker-network hostname, so identity is verified at the app layer
  (MTLSVerificationMiddleware's CN check) rather than via TLS hostname binding — disable
  check_hostname accordingly. Also switch the container healthchecks to hit https with the same
  client cert.

- **chembl**: Degrade gracefully on partial get_chemistry failure
  ([`679ee2b`](https://github.com/athril/agentic-target-evidence/commit/679ee2bcb7987dede46f40755271c3a2104105de))

Gather the mechanism/activity/potency/clinical calls with return_exceptions=True so a timeout or 5xx
  on one (e.g. the slow potency query) only drops that signal instead of discarding the whole
  chemistry bundle. Non-5xx client errors on the primary mechanism/activity calls still raise; the
  bundle is only reported unavailable when every call fails.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **core**: Retry HTTP GET/POST on timeout, not just disconnect
  ([`d4f05db`](https://github.com/athril/agentic-target-evidence/commit/d4f05db29d7ac8cc923ee08b70019a692ff70cd9))

httpx.TimeoutException was previously left unretried, so a single slow upstream response (common for
  the new bulk-data sources) failed the whole call immediately instead of getting the same backoff
  as RemoteProtocolError.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **lenses**: Recover lens verdicts from premature root-object closure
  ([`c9537c3`](https://github.com/athril/agentic-target-evidence/commit/c9537c3ea0ce582c94b9e5985c19e1bd540f22b6))

Local models occasionally close the verdict's root JSON object early and emit the remaining keys
  (e.g. axes) as trailing siblings. Strict json.loads raised "Extra data" on the leftover and
  discarded an otherwise-valid verdict entirely; loads_recovering splices out the stray closing
  brace and retries.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **lenses**: Tolerate raw newlines in LLM verdict JSON
  ([`a4a70a0`](https://github.com/athril/agentic-target-evidence/commit/a4a70a0078050f45c5324b94dbe75e3e82cd7f3c))

Local models emit multi-paragraph prose fields (e.g. the biology lens's mandated 2-4 paragraph
  narrative) with literal newlines between paragraphs rather than \n escapes. Strict json.loads
  rejects raw control characters inside strings, silently discarding an otherwise valid verdict as
  "could not be parsed" — switch to strict=False decoding.

- **mcp**: Add missing FastMCP server entrypoint for ClinGen
  ([`98bbb86`](https://github.com/athril/agentic-target-evidence/commit/98bbb864a7e60b65599a23809c344bdde0782b28))

Every other evidence source ships a server.py alongside tools.py; ClinGen was tools-only since its
  initial commit, leaving it unusable as a standalone MCP server even though the genetics agent
  already consumes get_clingen_validity in-process.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Bound GWAS Catalog SNP concurrency; add PubMed journal/ISSN fields
  ([`8ee6a78`](https://github.com/athril/agentic-target-evidence/commit/8ee6a7822abe33ca376ca5f8dce42140aa8292d3))

GWAS Catalog: unbounded per-SNP association fetches could open hundreds of simultaneous connections
  to the EBI API and trigger read timeouts; bound to 10 concurrent requests via a semaphore.

PubMed: capture full_journal/issn/essn from ESummary so the new source-quality scoring agent can
  match journals against SJR/OpenAlex by ISSN instead of by fuzzy abbreviated-name matching.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **source_quality**: Guard SJR score formatting when matched row has no numeric score
  ([`f8fa9ef`](https://github.com/athril/agentic-target-evidence/commit/f8fa9efafa4b8d5be7efe38f7744b8aa5600c323))

resolve_sjr can return matched=True with sjr=None when a bundled Scimago row has a quartile but a
  blank score, which crashed quality_note formatting with f"{sjr.sjr:.2f}". Also wires
  disease_tissue_expression_note (and the rest of the tissue-context fields) into
  biology_lens/safety_lens contracts so validate_inbound doesn't reject them.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

### Chores

- Fix pre-existing ruff lint errors
  ([`c38a611`](https://github.com/athril/agentic-target-evidence/commit/c38a611b9be799c71eb75b30ea1dcf01304c0b73))

Removes an unused build_trial_facts import in workflow.py, switches pack_batches to PEP 695 generic
  syntax (UP047), fixes an unsorted import block in test_source_quality_wiring.py, and drops unused
  os/tempfile imports in test_scimago.py. All pre-dated the recent feature commits.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Harden gitignore against results data and scratch output
  ([`5128339`](https://github.com/athril/agentic-target-evidence/commit/51283394ca10f342f2006b724865518270a0c7bb))

Broaden results-related ignores to results/, results-*/, and results_*/ so any regenerated or
  timestamped output directory is excluded by default, superseding the narrower per-subdirectory
  globs.

- Project scaffolding and dev tooling
  ([`e45c434`](https://github.com/athril/agentic-target-evidence/commit/e45c43412d6958f417bf90f3276dcb33ef8049ba))

uv-managed pyproject.toml + lockfile, pre-commit hooks (with the check-added-large-files schema/size
  fix), commitlint config, and an .env.example covering every provider/service the stack can route
  to.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Remove dead commented-out code and stale backward-compat comments
  ([`2de5c2a`](https://github.com/athril/agentic-target-evidence/commit/2de5c2ad4168c9cf129f999740046222352b2b96))

langfuse.py: drop a duplicate commented-out copy of the module left over from an earlier edit.
  evidence.py: trim "additive/1.0 rows still validate" comments now that there's no pre-1.0 schema
  in the wild to stay compatible with. No behavior change in either file.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Remove unimplemented results/experiment artifact path
  ([`3b8dbb5`](https://github.com/athril/agentic-target-evidence/commit/3b8dbb5b98a04548561cf8b5029081e1dd8aab01))

ExperimentAgent persists rankings to the Postgres experiments table only and never wrote a file
  artifact, so the mounted/created directory was dead.

- Rename CLI entry points atv-mcp/atv-chat to target-evidence-mcp/target-evidence-chat
  ([`c1370ce`](https://github.com/athril/agentic-target-evidence/commit/c1370ce0f96f813de484928c716b67e230a69612))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Strip PM/milestone ticket labels from comments and docstrings
  ([`7064881`](https://github.com/athril/agentic-target-evidence/commit/7064881b45d65fa25bf8eb5d339f01a8538b55b5))

Remove leftover scaffolding artifacts (MP-NN task ids, WS# workstream tags, "out of scope for
  current milestone", "bench/eval workstream", "Phase 6 decision") from test docstrings and source
  comments. These are project-management references that don't belong in the codebase; the wording
  is reworded to be self-describing instead. No behavior change.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Update stale "five lenses" references to six
  ([`0c03f00`](https://github.com/athril/agentic-target-evidence/commit/0c03f002b66feecd84e6597cc64a91b22fbac945))

Docstring/comment-only cleanup after the regulatory lens was added — no functional change.

### Code Style

- Apply ruff format to source and tests
  ([`c197483`](https://github.com/athril/agentic-target-evidence/commit/c1974834349557caac69ecb3db6270f15cc24c72))

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Continuous Integration

- Remove automatic Claude PR review workflow
  ([`90bcbe8`](https://github.com/athril/agentic-target-evidence/commit/90bcbe87c7821591d2a07287d17e7761211e8d98))

Drop claude-code-review.yml so Claude no longer auto-reviews every incoming pull request. Reviews
  are now opt-in via the @claude mention handler in claude.yml, avoiding unattended token spend.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Documentation

- Add NOTICE.md with license, data-source, and AI-disclaimer notices
  ([`b9d826e`](https://github.com/athril/agentic-target-evidence/commit/b9d826e865441cee184f13b5cb3e4f3f07adfb13))

Apache 2.0 NOTICE covering third-party dependency licenses, public data source terms (including the
  SCImago/OMIM non-commercial carve-outs), and a disclaimer on AI-generated output, data routing for
  SENSITIVE evidence, and cloud LLM provider terms.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Add top-level README
  ([`9b4685d`](https://github.com/athril/agentic-target-evidence/commit/9b4685d136d2ea35c61dd92bff62c84df6dbd463))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **lenses**: Clarify source-quality scoring semantics for structured evidence
  ([`fda578c`](https://github.com/athril/agentic-target-evidence/commit/fda578c187e48a8abccaac910cd0e9ac61404580))

The score field already maps Q1-Q4/preprint to numeric weights, but lenses were still told to
  down-weight by quartile/preprint flags directly. Spell out the score scale and call out that
  score:1.0 with quartile:null means structured/database evidence (no journal to rank), not an
  unscored source, so lenses stop treating it as lower-confidence by mistake.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **notice**: Clarify lens-file revision behavior and capability assessment
  ([`0d774a0`](https://github.com/athril/agentic-target-evidence/commit/0d774a0be3db0615c2df85a095a3eca7ec9cb81e))

Documents that lens markdown files under lenses/ may be rewritten in place during a run as
  review/critic agents revise the verdict — only the post-completion version is authoritative. Also
  notes that current capability, even with smaller/local models, is approaching a junior
  translational scientist's first pass at this evidence-gathering and triage work, without changing
  the standing expert-review disclaimer.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **notice**: Document DGIdb and TTD data sources
  ([`154a7a7`](https://github.com/athril/agentic-target-evidence/commit/154a7a722dbe4ddf79de183520480359f7d5d729))

Add DGIdb (curated drug-gene interactions, per-claim source licensing) and TTD (target
  development-stage classification) to the data-sources list, and note TTD's unconfirmed
  commercial-use terms in the commercial-use section alongside the other non-commercial-gated
  sources.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Features

- **agents**: Challenge (critic/reviewer) and synthesis (experiment, report)
  ([`0a77857`](https://github.com/athril/agentic-target-evidence/commit/0a77857fbf2c767a8634e9058d98da1c3f747d49))

critic: independent-retrieval fact-check pass over lens verdicts. reviewer:

final consistency/completeness pass. experiment: proposes confirmatory experiments, gated on the
  Mendelian suitability floor. report: renders the dossier; citations.py is the shared formatting
  helpers used by both this and the per-lens report (lens_report.py, landing once interpretation
  lenses do).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **agents**: Retrieval agents and retrieval services
  ([`388b239`](https://github.com/athril/agentic-target-evidence/commit/388b2390b565081882ce69ecf79329a82f76f24e))

Nine retrieval agents (literature, patent, clinical_trial, genetics, omics, functional,
  druggability, opentargets, openfda) plus their backing services/retrieval/* operators, wired to
  the MCP servers committed earlier.

Includes agents/_common.py and services/_common.py (shared provenance/result helpers used by every
  agent and service module from here on) and tests/agents/conftest.py — pulled forward from later in
  the plan since pytest needs both present to even collect this batch's own tests, and neither has
  any forward dependency.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **agents**: Screening, knowledge extraction, and source-quality agents
  ([`91eac74`](https://github.com/athril/agentic-target-evidence/commit/91eac74a0f2ef0632e49655c442df78ae00f70b9))

screening: applies keep/drop/uncertain verdicts to gathered evidence.

knowledge_extraction: full-text upgrade + embeddings for kept evidence.

source_quality: SJR/OpenAlex journal-quality scoring, falling back to the LLM's predatory-journal
  judgment when neither source resolves a journal.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **biology-lens**: Condense uninformative DepMap blocks and add relevance guard
  ([`c9dd678`](https://github.com/athril/agentic-target-evidence/commit/c9dd67803ca9a48b0458f7e4f93d811ad0c81910))

For a non-oncology target with no meaningful cancer-cell-line dependency, the biology lens now
  collapses the per-lineage DepMap table to a one-line caveat instead of a full block the model has
  to talk its way out of, and a new post-LLM guard annotates verdict text that still cites DepMap
  essentiality as mechanism support despite that. Also hardens the constraint guards: the mis_z
  inversion check now catches magnitude-based tolerance claims (not just the "high"/"elevated"
  label), and haploinsufficiency negation handling no longer false-positives on the correct "NOT
  haploinsufficient" band phrasing.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **checkpoint**: Allowlist project pydantic models for msgpack checkpoint serialization
  ([`03d6746`](https://github.com/athril/agentic-target-evidence/commit/03d674629c5997b925c74b65ef2b12a0f6c7c904))

LangGraph's default JsonPlusSerializer allows-with-warning any module/class it doesn't recognize
  when msgpack-serializing checkpoint state, and will start blocking them once
  LANGGRAPH_STRICT_MSGPACK becomes the default. Explicitly allowlist the schemas.* types that flow
  through PipelineState so checkpointing keeps working without relying on that warning.

- **commercial**: Add market-size axis from Orphanet prevalence
  ([`78e8a6b`](https://github.com/athril/agentic-target-evidence/commit/78e8a6b302e3fc580c4bad9a6f071b655e4b70ef))

Give the commercial lens a third axis that sizes the addressable patient population from Orphanet
  disease-prevalence bands. The workflow summarizes the prevalence evidence rows into
  orphanet_prevalence_text fed to the lens, with guidance to read the prevalence band (not the
  disorder name), to treat a missing record as uninformative rather than rare, and to prefer the
  validated/worldwide record when several disagree.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **commercial-lens**: Add competitive-landscape framing and overstatement guard
  ([`6fd7109`](https://github.com/athril/agentic-target-evidence/commit/6fd710971d2a07f45d5b4e91b7b54d0b2e20dc51))

Pre-computes the approved/clinical/preclinical competitive ladder and target-vs-indication
  whitespace framing so the LLM has accurate language to draw from, then adds a post-LLM guard
  (mirroring the constraint/clinical guards) that annotates residual commercial overstatements:
  blanket "no drugs target X" claims, indication-level "underserved" claims drawn from target-level
  evidence, and "market size unknown" asserted from Orphanet silence alone.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **core**: Direction-scoped raw archive layout, sorted/relativized summary.csv
  ([`7e62c90`](https://github.com/athril/agentic-target-evidence/commit/7e62c9000d5f2b9320f1d8ca0a18c2768da3ed21))

archive_raw() now writes under results/data/<gene>/<disease>/<direction>/... instead of
  results/original/<gene>/..., merging the raw-source archive into the per-direction
  derived-artifact tree. export_summary_csv() gains the same direction segment, hoists run-constant
  fields into a leading comment line, sorts rows by (evidence_type, source), and relativizes
  artifact_uri to the CSV's own directory.

Note: architecture.md and docs/{howto_run,setup,adding_evidence_sources}.md still describe the old
  results/original/ split — doc pass is deferred per the current commit-batch plan and needs to
  reconcile this.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **core**: Postgres persistence, baseline migration, checkpointer
  ([`f7d757c`](https://github.com/athril/agentic-target-evidence/commit/f7d757cf72c22fb3e09d132179c0a32dfc24aa3e))

Single-revision Alembic baseline (Postgres is the system of record; results/ artifacts are derived
  and regenerable from it), the LangGraph Postgres checkpointer, and the core utils (exceptions,
  http, json, evidence-text helpers) the rest of the stack depends on.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **core**: Routing, telemetry, and A2A transport
  ([`8fb6b83`](https://github.com/athril/agentic-target-evidence/commit/8fb6b83ab0c2ea5690dda730f190f427aced8912))

Config-driven model routing (hybrid/all_local/custom — agents call the router, never a provider
  directly), Langfuse/OTel telemetry setup, and the mTLS A2A client/server pair. Includes the fix
  for A2AClient's SSL context handling: httpx's verify= takes a stdlib ssl.SSLContext, not an
  httpx-native type, and explicit ssl_context=None must be honored as "no mTLS" rather than falling
  back to env-based cert auto-discovery.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **decision**: Reconciler and Mendelian suitability scoring
  ([`02a2e0f`](https://github.com/athril/agentic-target-evidence/commit/02a2e0fa9915f8a3e4f686ebbadfd9f970def17f))

reconciler.py builds the cross-lens AgreementMap from LensVerdicts. suitability.py applies a
  Mendelian-disease causality score floor, driven by config/scoring.yaml so the thresholds aren't
  hard-coded (rule #4).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **druggability**: Add DGIdb interactions and TTD development stage
  ([`3b1a225`](https://github.com/athril/agentic-target-evidence/commit/3b1a225ed8692f8a6af987ec240fec825ba4d849))

Wire the DGIdb and TTD MCP connectors into the druggability retrieval service alongside the existing
  UniProt + ChEMBL chain. DGIdb adds curated drug-gene interaction claims and druggable-genome
  gene-category annotations (additive enrichment, degrading gracefully on outage); TTD adds target
  development-stage classification, gated behind TTD_ENABLED and skipped entirely when off.

The biology lens gains a developability axis that reads UniProt subcellular localization, ChEMBL
  clinical-candidate progression, and TTD status as a modality-specific, secondary signal, plus
  guidance that knowledge graphs corroborate association not mechanism and that raw variant counts
  are supportive rather than primary evidence.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **evidence**: Add disease-class taxonomy and evidence-hierarchy weighting
  ([`7265512`](https://github.com/athril/agentic-target-evidence/commit/7265512686b6b2a4491ed674165b8acffb40772a))

Replaces the oncology-only binary (_ONCOLOGY_AREA_IDS) with a config-driven disease-class resolver
  (oncology/metabolic/fibrosis/rare_mendelian/...) and a disease-class-conditional evidence-strength
  hierarchy for structured claims, so lenses can reason uniformly across disease classes instead of
  via scattered hardcoded rules.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **evidence**: Claim pipeline + KG builder services
  ([`b74eb0d`](https://github.com/athril/agentic-target-evidence/commit/b74eb0df6402ec2bc90633b5f960a12ef8e6454b))

src/services/evidence/**: claim extraction (full-text -> atomic CoreClaim rows), claim clustering,
  constraint interpretation (gnomAD LoF/missense -> plain-language grade), mouse phenotype rendering
  (IMPC), disease/tissue matching, entity resolution, source quality/sufficiency scoring.

src/services/knowledge_graph/**: offline KG builder/export/ingest/query — this is the
  schemas.knowledge_graph-backed graph service, not the SPOKE MCP server (SPOKE is an external KG
  queried as a retrieval source; this one is built from this run's own evidence).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **gbd**: Add GBD/IHME disease-burden source and auto-keep its evidence in screening
  ([`d32fdbf`](https://github.com/athril/agentic-target-evidence/commit/d32fdbf666f4a4a287076856ec0d7d18c5f5044b))

Adds GBD (Global Burden of Disease) as a disease-keyed, non-commercial-gated epidemiology source
  mirroring the OpenFDA/Orphanet/SCImago patterns: a CSV-based connector under mcp_servers/gbd, a
  fetch_gbd retrieval service, a gbd acquisition node feeding the commercial lens's market-size
  axis, and gateway tool-list gating consistent with omim/scimago/ttd.

Also fixes a screening gap that silently dropped GBD evidence: EvidenceType.EPIDEMIOLOGY was missing
  from screening's _AUTO_KEEP_TYPES, so GBD rows (which carry no gene mention) were going through
  the LLM relevance screener instead of being auto-kept like Orphanet's structurally-identical
  GENETICS-typed prevalence evidence — causing them to be dropped before ever reaching the
  commercial lens.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **genetics**: Surface ancestry-generalizability signals
  ([`e691ffe`](https://github.com/athril/agentic-target-evidence/commit/e691ffec70dd93acbb35fb7d0db8b2e67079b660))

Carry GWAS cohort composition (initial_sample_size) into the genetics lens's source-evidence text
  and flag gnomAD HC pLoF variants whose allele frequency varies >3x across well-sampled populations
  as ancestry-skewed. The genetics lens skill gains an ancestry-generalizability caveat for
  single-ancestry GWAS support and for ancestry-skewed natural-knockout signal — framed as a
  confidence caveat, never a verdict reversal, and never applied to Mendelian/ClinVar causality.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **harness**: Base agent, contracts, loop guard, skills loader
  ([`9dffea4`](https://github.com/athril/agentic-target-evidence/commit/9dffea4870e71c9cb530036c222d7be647271f2d))

BaseAgent enforces each agent's consumes/produces contract (minimal information per agent) and wires
  the loop-guard counters that cap loop-capable edges — agents inherit this rather than
  reimplementing the guarantees themselves.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **infra**: Auto-pull Ollama models on first startup
  ([`57d7d1b`](https://github.com/athril/agentic-target-evidence/commit/57d7d1be07fa1feb8c8ed652badc7996c5f94465))

Add a one-shot `ollama-pull` compose service that pulls the chat/agent model and the embedding model
  into the ollama-models volume once Ollama is healthy, so a fresh clone is self-provisioning (no
  manual `ollama pull`). Mirrors the existing minio-setup one-shot pattern and is a no-op once the
  volume already holds the weights.

- ollama gains a `healthcheck` (ollama list) that ollama-pull gates on. - chat and planner now wait
  on ollama-pull completing, so the model is present before they serve (avoids first-request 404s).
  - Makefile APP_SERVICES includes ollama-pull. - .env.example documents OLLAMA_EMBED_MODEL and the
  auto-pull behavior.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **interpretation**: Genetics/biology/safety/clinical/commercial/regulatory lenses
  ([`5418d74`](https://github.com/athril/agentic-target-evidence/commit/5418d740811d5774f40d251b1345264bd7dd1954))

Six interpretation lenses share _lens_base.py (lens-evidence-type routing, LLM verdict parsing,
  source-quality-aware confidence). genetics_lens consumes SPOKE/MONDO context from the genetics
  agent; biology_lens and commercial_lens consume the constraint/patent-landscape interpreters from
  the evidence services batch.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **interpretation**: Make lens context window and max-claims configurable
  ([`74bc77a`](https://github.com/athril/agentic-target-evidence/commit/74bc77a9f770c208c437a1ad9ae9fadccf03a44d))

The six interpretation lenses can blow past the local model's context window on evidence-heavy
  targets. Two knobs to tune this without code changes:

- LENS_MAX_CLAIMS (env) caps how many claims a single lens call receives; claims are already ranked
  best-first by evidence weight, so the cap drops the weakest. Replaces the hard-coded
  _MAX_CLAIMS=100 with _max_claims(). - LENS_NUM_CTX (env) overrides num_ctx for all six lens tasks
  uniformly, winning over their per-task entries in config/routing.yaml task_num_ctx.

routing.yaml gains explicit per-task num_ctx for each lens (32768) plus the new investigator task
  (16384). Covered by routing-policy and lens-base tests.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **lenses**: Post-llm output guards for hallucinated claims
  ([`9b0bc1e`](https://github.com/athril/agentic-target-evidence/commit/9b0bc1efe1776e78a4362dfe43d8b4fbd0e1baef))

Add a shared post-LLM safety net that annotates (never silently rewrites) lens
  narrative/rationale/axis text contradicting authoritative structured evidence, recording a
  ValidationFlag on every activation:

- Constraint guard (genetics + safety): flags unsupported haploinsufficiency and inverted
  missense-constraint / mis_z-direction claims against the precomputed gnomAD bands. Widens the
  regex coverage (qualifier-first "strong missense constraint", "high mis_z" inversion) with
  negation lookback, and passes the full ConstraintReading to the lens. - Clinical-phase guard
  (clinical): new deterministic trial-fact extraction flags phase-count conflation and per-trial
  phase/recruitment-status mismatches. - Tissue-relevance guard (biology + safety): flags treating a
  high-bulk-TPM, non-disease tissue as disease-relevant.

Safety-lens skill gains rules distinguishing on-target extra-tissue from off-target liabilities,
  naming mouse-KO organ phenotypes as candidate liabilities, and not re-banding constraint or
  reading bulk-TPM rank as relevance.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **lenses**: Resolve and wire disease-class through all 6 lenses
  ([`b5d82ff`](https://github.com/athril/agentic-target-evidence/commit/b5d82ff0e67313766619bfbce8b789c8bb674229))

Replaces the oncology-only binary previously computed ad hoc in the genetics and biology lens nodes
  (_ONCOLOGY_AREA_IDS) with a single _resolve_disease_classes call per lens node, feeding the
  disease_class taxonomy/rules added in the previous commit. Each lens now receives disease_classes
  in its task_spec and injects the matching guidance note via build_disease_class_note. The shared
  claim ranking/truncation in _lens_base.py also switches from a flat non-literature weight to the
  disease-class-conditional evidence_weight hierarchy, and injects an evidence-strength ledger into
  the prompt so the LLM sees which evidence categories are strongest before reasoning over claims.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Add DGIdb MCP server for curated drug-gene interactions
  ([`e3d9b96`](https://github.com/athril/agentic-target-evidence/commit/e3d9b962892bd447d1fde1c96a4c3e07ef50d97b))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Add MCP gateway and Gradio chat assistant
  ([`6bfd991`](https://github.com/athril/agentic-target-evidence/commit/6bfd9913c386e7c0bfb68e6d008ff2893802402e))

Compose every public mcp_servers/* connector into one FastMCP gateway (atv-mcp) that external MCP
  hosts can call ad hoc, and a Gradio chat assistant (atv-chat) that drives those tools via a local
  Ollama model.

The gateway discovers sources by walking mcp_servers/ and fails closed on any SENSITIVE-classified
  source that grows an importable server.py; the now server-less internal_data source is removed so
  it can never be exposed. Optional bearer auth (MCP_GATEWAY_TOKEN) guards the HTTP transport, and
  the chat assistant persists per-user conversation state via the LangGraph Postgres checkpointer
  with optional CHAT_AUTH login.

Both ship as Docker services (mcp-gateway internal-only, chat on :7860), with Make targets, a
  Windows make.bat wrapper, and a chat dependency group.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **mcp**: Add TTD MCP server for therapeutic target development status
  ([`762b389`](https://github.com/athril/agentic-target-evidence/commit/762b389373e395df08a022b1859d98a4d4b71f69))

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Add UniProt MCP server for protein subcellular localization and class
  ([`841cf28`](https://github.com/athril/agentic-target-evidence/commit/841cf2892aaca471d6b6c7b5b0e35d607e756bc4))

Split out of the former druggability server alongside chembl.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Clingen MCP server
  ([`9d92f5a`](https://github.com/athril/agentic-target-evidence/commit/9d92f5ab6d3ecda7660386b9eda3e801c507e5bf))

Gene-disease validity classification lookups (tools-only — no standalone FastMCP server entrypoint
  for this source yet).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Clinicaltrials.gov MCP server
  ([`1346972`](https://github.com/athril/agentic-target-evidence/commit/134697237f39fa73d2053c39ac9ae15af694c204))

FastMCP tools for searching ClinicalTrials.gov by gene/disease and extracting phase, status, and
  outcome data.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Depmap MCP server
  ([`3b2b8c7`](https://github.com/athril/agentic-target-evidence/commit/3b2b8c7186d5473be68185373af1a244830a742c))

FastMCP tools for CRISPR/RNAi gene-dependency data — bulk-file fetch with lineage breakdown, falling
  back gracefully to summary-only data when the bulk download is unavailable.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Druggability MCP server
  ([`b75f8df`](https://github.com/athril/agentic-target-evidence/commit/b75f8df5c4024ff929a86f08ade4d977021cffc8))

FastMCP tools for protein-class and chemistry data (UniProt/ChEMBL) — the substrate for the
  druggability assessment lens.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Encode MCP server
  ([`2f65b08`](https://github.com/athril/agentic-target-evidence/commit/2f65b0855553a211d35e681cd210bcf44bfb39b4))

Cis-regulatory element assay coverage at a gene's locus, backing the new regulatory_element evidence
  type.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Expression Atlas MCP server
  ([`e3baac0`](https://github.com/athril/agentic-target-evidence/commit/e3baac069101026aff05fb655b7b266aad409529))

Tissue/cell-type expression baseline data (EMBL-EBI), complementing GTEx.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Gencc MCP server
  ([`d7e7c52`](https://github.com/athril/agentic-target-evidence/commit/d7e7c520a940d45a8209df769e9593ef0a09387a))

Gene-disease validity classifications (curated consensus across ClinGen, PanelApp, and other
  submitters).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Gnomad MCP server
  ([`658c92e`](https://github.com/athril/agentic-target-evidence/commit/658c92e51589ae40292a0e165bf3c591cdd5407e))

FastMCP tools for gnomAD LoF/missense constraint and ClinVar variant lookups (accessed via the
  gnomAD API).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Gtex MCP server
  ([`f5c5e85`](https://github.com/athril/agentic-target-evidence/commit/f5c5e85ca450805041374dac452942774e8d8d97))

FastMCP tools for GTEx tissue expression queries.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Gwas Catalog MCP server
  ([`62f998d`](https://github.com/athril/agentic-target-evidence/commit/62f998d16c62f8c40481c21b950d3f3f25031c2b))

FastMCP tools for GWAS Catalog variant/trait association lookups by gene and rsID.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Impc MCP server
  ([`790162b`](https://github.com/athril/agentic-target-evidence/commit/790162b4b934640e17fdfb25f2e0bf1287c10fa3))

Mouse knockout phenotype data from the International Mouse Phenotyping Consortium.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Internal-data MCP server
  ([`c7b9e1d`](https://github.com/athril/agentic-target-evidence/commit/c7b9e1d6282aecdedc6cb732ad7563159f779af4))

Tags every record it emits as SENSITIVE — this is the boundary for proprietary/internal evidence
  sources, distinct from the public-data servers around it.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Omim MCP server
  ([`19f4907`](https://github.com/athril/agentic-target-evidence/commit/19f49072b4a66d25f60b0571a130df37b75a299d))

Gene-disease phenotype/inheritance lookup. Non-commercial-use API key gated behind OMIM_ENABLED
  (default off) per OMIM's terms; the genetics agent skips the source entirely when unset, no error.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Ontology MCP server (HGNC + MONDO)
  ([`dd52abe`](https://github.com/athril/agentic-target-evidence/commit/dd52abefde5750e8e9ddc1e680dc4a6327e3e6f2))

Identifier resolution for gene symbols (HGNC) and disease terms (MONDO) — the canonical
  gene_id/disease_id source the retrieval agents and lenses key off of.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Open Targets MCP server
  ([`add9238`](https://github.com/athril/agentic-target-evidence/commit/add92388ae1b5271c94d6e73dbe3dd0dd8d8a1d8))

FastMCP tools for the Open Targets Platform GraphQL API — target-disease association scores and
  supporting datatype breakdowns.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Openalex MCP server
  ([`d5d5292`](https://github.com/athril/agentic-target-evidence/commit/d5d52929188f711122df54d60bee94c15e93aa01))

CC0 journal-quality signal (citation counts, works metadata) by ISSN — the commercial-safe default
  for the new source-quality scoring agent. Enabled by default; OPENALEX_MAILTO is optional and
  joins the API's polite pool.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Openfda MCP server
  ([`a623dd1`](https://github.com/athril/agentic-target-evidence/commit/a623dd1220085ce8b0d5a034782bf529ab9f0369))

FastMCP tools for FDA drug label and FAERS adverse-event signal lookups — feeds the regulatory lens.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Orphanet MCP server
  ([`a74eb08`](https://github.com/athril/agentic-target-evidence/commit/a74eb08930b5717a603e2255bf456ec73bfde5fd))

Rare-disease gene-disease associations and prevalence data.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Project Score MCP server
  ([`9d333cd`](https://github.com/athril/agentic-target-evidence/commit/9d333cd043f1d7f5abc4b80b1ea7130a36c45568))

Sanger Project Score CRISPR dependency data, complementing DepMap.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Pubmed MCP server
  ([`fc10803`](https://github.com/athril/agentic-target-evidence/commit/fc1080355cedd4871e730400bb1d3fdba66f79ba))

First of the MCP retrieval servers — FastMCP tools for PubMed/MEDLINE search and abstract/full-text
  fetch, using resolved MeSH descriptors rather than untagged free-text terms.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Scimago MCP server
  ([`bbd53db`](https://github.com/athril/agentic-target-evidence/commit/bbd53db45f922846f1363c66641f73226b77a1c1))

SJR journal-rank lookup by ISSN. The bundled SJR dataset is non-commercial-licensed (see NOTICE.md),
  so SCIMAGO_SJR_ENABLED defaults to false and the gzipped index is gitignored — non-commercial
  users regenerate it locally via scripts/build_scimago_index.py. When disabled, source-quality
  scoring falls back to OpenAlex + the LLM's predatory-journal judgment.

Also documents the OMIM and OpenAlex env toggles added alongside this source.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Spoke MCP server
  ([`160ae6b`](https://github.com/athril/agentic-target-evidence/commit/160ae6bcb5c8c43ca9f2cf11850fb242a92bc254))

FastMCP tools for querying the SPOKE biomedical knowledge graph — the last of the 14 MCP retrieval
  servers.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Uspto MCP server
  ([`672e42c`](https://github.com/athril/agentic-target-evidence/commit/672e42c2a73dc3c888cbf5435c289c603684289f))

Patent search against the USPTO Open Data Portal with PDF abstract extraction (+ OCR fallback for
  scanned documents). Search results always carry a plain Google Patents link for manual lookup, but
  this software never fetches from or scrapes Google Patents.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **orchestration**: Target-validation pipeline, planner, runtime config, containers
  ([`d83ea9a`](https://github.com/athril/agentic-target-evidence/commit/d83ea9a718c73136766b48747e5faefa4e83de96))

Wires the full target-validation LangGraph workflow (capabilities/target_validation), the Planner
  agent (user touchpoint), the A2A service runner, and the dev/runtime scaffolding needed to
  actually execute a pipeline run: routing.yaml, otel-collector.yaml, disease_tissue.yaml,
  Docker/Compose, CI, Makefile, run_analysis.py CLI entrypoint, and the smoke/capability test
  suites.

Also includes the placeholder capabilities (indication_expansion, competitor_monitoring,
  target_prioritization) and two standalone dev scripts (scimago index builder, USPTO abstract OCR)
  that have no better home in the batch sequence.

Documentation (AGENTS.md, CLAUDE.md, NOTICE.md, architecture.md, docs/) is intentionally excluded
  from this batch and deferred to a later documentation-reconciliation pass.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **report**: Split dossier into Literature vs Empirical sections, sort literature by
  quality+recency
  ([`fda9931`](https://github.com/athril/agentic-target-evidence/commit/fda99310078f659ad504005a7efd7bcf3d7a9f89))

Both the per-lens report and the full dossier now group evidence into a top-level Literature section
  and a top-level Empirical section (patents, trials, genetics, omics, etc.), instead of one flat
  list of same-level sections. citations.is_literature() is the shared predicate so the two
  renderers never drift. Literature rows sort highest-quality-first then most-recent-first so
  citation numbers and reading order agree across both report types; quality stars now also show on
  patent/trial/generic empirical rows.

- **retrieval**: Concurrent source fetches and Orphanet prevalence
  ([`b0c315c`](https://github.com/athril/agentic-target-evidence/commit/b0c315c87b1014b8839e68aad7b753d1a197c23c))

Restructure the genetics and omics retrieval agents to fan out their independent source calls with
  asyncio.gather in dependency tiers, each call wrapped so one source outage degrades to None/[]
  instead of discarding the evidence already gathered from the others.

Add an Orphanet disease-prevalence source (product 9) — a new get_orphanet_prevalence tool plus its
  MCP server registration — fetched as a dependent tier from the OrphaCodes already resolved for
  gene-disease associations. Prevalence is an addressable-population signal, archived as its own
  evidence row distinct from genetic-validity associations.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **routing**: Token-budget-aware batch packing + per-task Ollama context override
  ([`534cefb`](https://github.com/athril/agentic-target-evidence/commit/534cefb5a1fabb0ac81d33310ab42e9f85718e8b))

Evidence text length varies a lot by source, so fixed item-count batches risk overflowing the
  model's context window. Pack screening and claim_extraction batches by estimated token count
  (core/batching.py) and let Ollama use a smaller num_ctx per task instead of the full 32k window,
  which otherwise forces KV-cache spillover to CPU on 8GB VRAM.

- **schemas**: Canonical Evidence/AgentMessage/state/KG/verdict schemas
  ([`3f2ad9e`](https://github.com/athril/agentic-target-evidence/commit/3f2ad9e06035bfbc3df24498d7377e6d23cbc3e3))

The single source of truth for the wire format (A2A), the Postgres schema, and summary.csv — every
  other layer builds on these Pydantic v2 models rather than defining a parallel shape.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **schemas**: Literature lens-topic routing, regulatory_element type, source-quality fingerprint
  ([`4b33997`](https://github.com/athril/agentic-target-evidence/commit/4b339974763f235adfc0aeaff457d3bf7273ef0b))

Adds EvidenceType.REGULATORY_ELEMENT (ENCODE cis-regulatory assay coverage) and LensTopic
  (genetics/biology/safety/clinical) — CoreClaim.topics tags a free-text literature claim with the
  lenses it's relevant to, so one claim can fan out beyond the biology-lens literature catch-all.
  Additive/optional: existing rows with no topics still validate.

Adds source_quality_fingerprint(), a stable per-run cache key for the new source-quality scoring
  agent, and a source_quality dict field on PipelineState (evidence_id -> SJR/OpenAlex quality
  assessment).

Removes the now-unused core/extension split (ClaimExtension, split_claim, EXTENSION_FOR) — confirmed
  zero remaining callers; topics supersedes it as the mechanism for routing literature claims to
  lenses.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **screening**: Enrich second-pass uncertain items with PMC full text
  ([`151f989`](https://github.com/athril/agentic-target-evidence/commit/151f9890fd2e5b36ce8c3ca25308069a01f52ac8))

Pass 1 leaves an item "uncertain" precisely when the abstract isn't enough to decide. Previously
  pass 2 just re-screened the same abstract. Now, items left uncertain with a PMID get their PMC
  Open Access full text fetched and injected (capped at SCREEN_FULLTEXT_MAX_CHARS, default 8000) so
  pass 2 can re-judge on real content; items with no PMID or no OA text fall back to the abstract as
  before. Renames pubmed fetch_full_text -> fetch_pmc_record (the metadata/link lookup) and
  introduces a separate fetch_full_text(pmc_id) that downloads and parses the JATS body into prose,
  stripping references/figures/tables.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **source-quality**: Top-tier SJR scoring, preprint/non-literature scoring, quality-ranked claim
  truncation
  ([`e687599`](https://github.com/athril/agentic-target-evidence/commit/e687599948258a43130450aae116bf88f1459e8c))

- scimago.tools: journals in the top 3% by raw SJR score 1.0 instead of the flat Q1 score
  (_TOP_TIER_SCORE/_TOP_TIER_PERCENTILE). - source_quality agent: preprints score at the Q4 floor
  regardless of venue; structured/database evidence (no journal to rank) scores at the top-tier
  ceiling instead of going unassessed. - _lens_base: rank claims best-quality-first before the
  _MAX_CLAIMS cap (raised 40->100) so truncation drops the weakest claims rather than whatever
  landed past an arbitrary index; expose the numeric score to lenses alongside
  quartile/predatory/preprint. - planner: re-run SourceQualityAgent on _rerun_acquisition_task,
  since its cache key isn't keyed on the evidence set and would otherwise silently return the
  pre-rerun quality map.

- **synthesis**: Add Investigator agent to close review gaps via MCP tools
  ([`525b672`](https://github.com/athril/agentic-target-evidence/commit/525b6728bea05ee4f485742625940f9b04d7145c))

After gap_detection, the new InvestigatorAgent runs a single bounded ReAct loop over the retrieval
  MCP tools (same gateway the chat assistant uses) to resolve the specific lens gaps/conflicts the
  review surfaced. It is conclusion-enrichment only — its findings flow into a new "Investigation"
  section of the report, never back into screening/lenses — and it never breaks the run on failure.

Wiring: - New node investigator between gap_detection and report; gap_detection's proceed branch now
  routes proceed → investigator → report (replan still loops back to hitl_gate, bounded). -
  PipelineState gains investigation_summary / investigation_tools_used, seeded by the planner and
  run_analysis and cleared on restart-from. - ReportAgent renders the additive Investigation section
  (empty when skipped).

Also reworks the restart router: it is now a passthrough whose conditional edge (_restart_route)
  either fans out to all acquisition nodes (fresh run) or jumps to a single node (restart). A
  Command(goto=...) did not suppress the node's static out-edges in this LangGraph version, so a
  resume re-ran all acquisition; a conditional edge replaces the route instead. _all_raw_evidence
  now dedups by (evidence_type, source) so a re-acquisition can't double-feed claims to the lenses.

langchain-ollama moves from the chat extra to the main dependencies (the investigator's ReAct loop
  needs it in the pipeline image).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **synthesis**: Gap detection agent and per-lens report rendering
  ([`2c53e66`](https://github.com/athril/agentic-target-evidence/commit/2c53e667978e3f7b32b27e5b4c051453c7b27f8a))

gap_detection: identifies evidence gaps and triggers a bounded replan loop.

lens_report.py: renders the per-lens markdown report, sharing citation formatting with the full
  dossier (report/citations.py) and lens-evidence-type routing with the interpretation lenses
  (_lens_base.py) — lands here since it depends on both.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

### Refactoring

- Move run_analysis.py into the src/ package
  ([`53263b6`](https://github.com/athril/agentic-target-evidence/commit/53263b65da16084fb336baeabc921f05594a7d82))

Relocate the CLI entrypoint under src/ so it sits alongside the importable packages and resolves
  imports the same way the rest of the codebase does.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- Remove Tier A dead code (gating, scorers, redundant agent wrappers)
  ([`894bcca`](https://github.com/athril/agentic-target-evidence/commit/894bcca98901fb70d6f9e8631ec841c320e83884))

Removes code unreachable from any live entry surface (CLI/planner, MCP gateway, chat):

- capabilities/target_validation/gating.py — validate_transition() was never called; the real HITL
  gate is hitl_gate_node's interrupt(). README corrected. - agents/planner/contract.py — imported by
  nothing. - agents/retrieval/{druggability,openfda,functional}/ — redundant agent wrappers; the
  graph calls services/retrieval/* directly. -
  services/evidence/{sufficiency_scorer,quality_scorer}.py — tested in isolation but routed through
  by no pipeline node. - schemas/evidence.experiment_fingerprint() — defined but never called. -
  agents/planner/agent.py — drop dead create_app/_run_until_interrupt/ _resume_after_hitl
  (superseded by main.py); keep the request models and state helpers main.py imports.

Tests: delete test_gating.py and test_functional_agent.py; trim scorer and experiment_fingerprint
  cases. Retarget the planner API tests at the live agents/planner/main.py app (previously the suite
  only covered the removed create_app and main.py had zero coverage); drop the three create_app-only
  caller-supplied-ID cases that main.py does not implement.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

- **mcp**: Give every MCP tool an explicit source-prefixed name
  ([`711e4e4`](https://github.com/athril/agentic-target-evidence/commit/711e4e4dcacff70ceb78adb965eb4acab908b2ea))

Each @mcp.tool() across src/mcp_servers/*/server.py now carries an explicit name="<source>_<action>"
  (e.g. chembl_get_chemistry, clingen_get_validity), with redundant repeats of the source name
  stripped from the original function name. ontology/server.py's three tools are prefixed by their
  actual upstream database (hgnc_, mondo_, hpo_) instead of the umbrella folder name, since that
  module wraps three distinct sources.

mcp_gateway/server.py now mounts each sub-server without namespace=, since the explicit per-tool
  name already carries the prefix — this keeps tool names identical whether a client connects to a
  source's standalone server.py or to the composed gateway, and avoids double-prefixing (e.g.
  chembl_chembl_get_chemistry).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- **mcp**: Remove druggability MCP server
  ([`ed16e11`](https://github.com/athril/agentic-target-evidence/commit/ed16e11e313dd6be0769a34d077d2d3133b9fd93))

Split into separate chembl and uniprot MCP servers for clearer source attribution.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
