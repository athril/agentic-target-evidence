# CHANGELOG


## [0.1.5](https://github.com/athril/agentic-target-evidence/compare/v0.1.4...v0.1.5) (2026-11-05)


### Features

* **biogrid-orcs:** read the CRISPR screens that scored a gene ([33eb771](https://github.com/athril/agentic-target-evidence/commit/33eb7718e3bd2dbb09521990d419a744da664d84))
* **biology:** compute the perturbation direction from models of the disease ([7fb115e](https://github.com/athril/agentic-target-evidence/commit/7fb115eb9dabc67de1ff7a8eec61cdef6932d923))
* **biology:** read model perturbations from statements of the target or its ortholog ([5c4e5ee](https://github.com/athril/agentic-target-evidence/commit/5c4e5ee3879e98a234be0b337a2b25091b7442c2))
* **biology:** rule the mechanism axis on knockout data ([b4892f5](https://github.com/athril/agentic-target-evidence/commit/b4892f525259a68a3134ec3e4b1b43b2dea979d2))
* **biology:** set druggability by rule and give the lens each drug's modality ([9605b10](https://github.com/athril/agentic-target-evidence/commit/9605b10c7d763b77be63566b116ad88c6b8791a1))
* **cbioportal:** read somatic alterations from the TCGA PanCancer Atlas ([f148c9b](https://github.com/athril/agentic-target-evidence/commit/f148c9b9149e8e3c34d803d91bd1d7ed63011307))
* **claims:** build structured records' claims deterministically, LLM for free text only ([f38875c](https://github.com/athril/agentic-target-evidence/commit/f38875cba85c62e77cacdc1388ed1edebfdf1f51))
* **claims:** type each claim's effect and derive its direction in code ([cb289c3](https://github.com/athril/agentic-target-evidence/commit/cb289c330700a9e518c7db604f5804b79dcdbe3f))
* **clingen:** add dosage sensitivity curation ([179f2d1](https://github.com/athril/agentic-target-evidence/commit/179f2d1598836124f8d526e756674d75874f2ebe))
* **clinvar:** read ClinVar's pathogenic calls with the condition each is for ([8c577db](https://github.com/athril/agentic-target-evidence/commit/8c577db45fe5db3d41a15ba13bea31e6fe4ee7f6))
* **decision:** compute the suitability score instead of asking the model ([4d89e03](https://github.com/athril/agentic-target-evidence/commit/4d89e038cf6c68ee7dde12db0aa50db542ba9326))
* **direction:** read the disease's own genetic direction from Open Targets annotations ([b67597e](https://github.com/athril/agentic-target-evidence/commit/b67597eb1a38db56ffc423e3797657228ee7f1b1))
* **direction:** weigh literature by study design instead of counting votes ([10fe450](https://github.com/athril/agentic-target-evidence/commit/10fe450dc3206c62ee714e1ff0c4314e591a068c))
* **evidence:** a disease scope for text ([bafcf61](https://github.com/athril/agentic-target-evidence/commit/bafcf61985f8511e108545c7f4e6349ce2a2da72))
* **evidence:** read the direction of effect from drugs, labels and human genetics ([67ec8f2](https://github.com/athril/agentic-target-evidence/commit/67ec8f2ddde241301b0d0d9a05d27be129f0721c))
* **evidence:** typography-tolerant disease, trait and indication matching ([407bddf](https://github.com/athril/agentic-target-evidence/commit/407bddf0fdf380e94836d99fce8f9ab49003bec8))
* **extraction:** check model-typed claims against their source and record what was left out ([010e7c9](https://github.com/athril/agentic-target-evidence/commit/010e7c98ebb8e4d7587ec8208c5f40b814a3cc20))
* **extraction:** type literature claims from statements of grounded sentences ([72be613](https://github.com/athril/agentic-target-evidence/commit/72be6136517fd958ae2e88e173ed887e0590b6c8))
* **extraction:** type the target's change from drug data and two narrow fields ([42fcce1](https://github.com/athril/agentic-target-evidence/commit/42fcce1778ebbfe07cf7db41311e0c12488654ba))
* **gene-profile:** a constraint table on its release and transcript ([45e7eaf](https://github.com/athril/agentic-target-evidence/commit/45e7eaf67850dca0c52ee74e000f984431813f5b))
* **gene-profile:** a FAERS table that says what reports cannot show ([cc8c866](https://github.com/athril/agentic-target-evidence/commit/cc8c86616a83b6d4522f11066b2b372c01f6415b))
* **gene-profile:** a third relation between a source and a claim ([cea547e](https://github.com/athril/agentic-target-evidence/commit/cea547e3ac874c13fc951d71453f06d5db7994b0))
* **gene-profile:** a view of the field from the grounded papers ([031f01e](https://github.com/athril/agentic-target-evidence/commit/031f01ef0b9ec6d6c2f5d60cf126fffccedbcb17))
* **gene-profile:** add digest topics on activities, compartments and variants ([f1f01ca](https://github.com/athril/agentic-target-evidence/commit/f1f01ca897de70e3d427fb3a539755571a992167))
* **gene-profile:** check core claims against a search and audit sentences ([2c1f32f](https://github.com/athril/agentic-target-evidence/commit/2c1f32fac185609f29bab47659a0e884682f63f2))
* **gene-profile:** check numbers and names sentence by sentence ([304ff5d](https://github.com/athril/agentic-target-evidence/commit/304ff5d72da35ec1ce41746aa3c73fe4d3c6922c))
* **gene-profile:** check the summary claims against the run's records ([20d68c7](https://github.com/athril/agentic-target-evidence/commit/20d68c724b9e166137250f961ecfe7a4101a1943))
* **gene-profile:** choose the claims to test by where they stand ([178d901](https://github.com/athril/agentic-target-evidence/commit/178d90182381ad16f334a1d413f92a3baebbc1cb))
* **gene-profile:** cite-ranked literature digest and run information ([63018ac](https://github.com/athril/agentic-target-evidence/commit/63018acbd92da6e3e3b316767c3b45f396f5998a))
* **gene-profile:** classify how settled each mechanism step is ([a5cc122](https://github.com/athril/agentic-target-evidence/commit/a5cc1224909241580f831ba569b0f3f49deb0d85))
* **gene-profile:** count each claim's evidence from the records of its papers ([f83025a](https://github.com/athril/agentic-target-evidence/commit/f83025a28057e99064a0b96c9944c83ec3851cd2))
* **gene-profile:** each CRISPR screen's own call, side by side ([fe5b426](https://github.com/athril/agentic-target-evidence/commit/fe5b4264f402416f9cbc12d210b31323717b81e0))
* **gene-profile:** effect sizes stated in the abstracts ([c0624e5](https://github.com/athril/agentic-target-evidence/commit/c0624e5a8aed9eb884af6525de4ac1452ec558e5))
* **gene-profile:** estimate how common the gene's disease alleles are ([bc1e50b](https://github.com/athril/agentic-target-evidence/commit/bc1e50bbc94fa7aa9b0a4134a9a4cb25a22fbf86))
* **gene-profile:** expression by kind of evidence ([7965b34](https://github.com/athril/agentic-target-evidence/commit/7965b34aa19ab8ecb7aba6901824f4eda8d9ea22))
* **gene-profile:** fetch the label of every approved drug of the gene ([9d2b1d2](https://github.com/athril/agentic-target-evidence/commit/9d2b1d2968ab3d34796a6946bd111834ac53fc9b))
* **gene-profile:** flag STRING-only binding, bare association totals and "proven" ([94aef04](https://github.com/athril/agentic-target-evidence/commit/94aef0492a2b568e4eee2cc808b005ba8b3d583a))
* **gene-profile:** give each FAERS row its own request ([0ec589a](https://github.com/athril/agentic-target-evidence/commit/0ec589a3471288a0c4f7a9a56c61d55b2e4f9883))
* **gene-profile:** ground the literature by identifiers and build the allelic series ([5373a59](https://github.com/athril/agentic-target-evidence/commit/5373a59baa262a88b83b46666dec0a33fe78c11a))
* **gene-profile:** ground the literature by identifiers and publish the sense table ([4dc6312](https://github.com/athril/agentic-target-evidence/commit/4dc63127f0d519581bd1629494634d5622030361))
* **gene-profile:** human burden and CRISPR screens tables ([23fc677](https://github.com/athril/agentic-target-evidence/commit/23fc67732a1c7090013dc74ea53e63c43e623f67))
* **gene-profile:** keep safety evidence apart by kind ([b81b113](https://github.com/athril/agentic-target-evidence/commit/b81b113772004dfde3edab7ec4c38643131f8fd5))
* **gene-profile:** list the structures by resolution ([efa558f](https://github.com/athril/agentic-target-evidence/commit/efa558f49f208ab4decb6c113631affd405517f8))
* **gene-profile:** make template 4 the current template ([6ec5620](https://github.com/athril/agentic-target-evidence/commit/6ec562002b62d1cff9e314fb1e3dbfd945bc1196))
* **gene-profile:** mechanism steps can branch into parallel routes ([b889214](https://github.com/athril/agentic-target-evidence/commit/b889214eb56b14403200921967fb20d13904b516))
* **gene-profile:** mouse orthology, partners, reagents and the immune block ([d2e8004](https://github.com/athril/agentic-target-evidence/commit/d2e800482f446fc76f35ee3b9f53a8f4ce3b4c2a))
* **gene-profile:** name each ClinVar call's condition and each row's alleles ([60ac895](https://github.com/athril/agentic-target-evidence/commit/60ac895e047c6fd707e927feb9699bbe4f562cf1))
* **gene-profile:** one list of a target's drugs with a fixed stage rule ([697f923](https://github.com/athril/agentic-target-evidence/commit/697f923112ee31ffadaafabf013195003b273e63))
* **gene-profile:** one property per allelic-series column, with checked studies ([6fdf845](https://github.com/athril/agentic-target-evidence/commit/6fdf84583e70b9c523e53b44d724d5215e914a3d))
* **gene-profile:** programmatic tables and figures, researcher-first checks ([c1452aa](https://github.com/athril/agentic-target-evidence/commit/c1452aa3e27b2a3d28e055ec4b9a1ce74a9d335a))
* **gene-profile:** read expression against the organs the disease affects ([935f19a](https://github.com/athril/agentic-target-evidence/commit/935f19a0bbdbd8ceaf16418aba0a46d16d77a7e8))
* **gene-profile:** read LOEUF by gnomAD's v4 cutoffs and keep measures apart ([03928e1](https://github.com/athril/agentic-target-evidence/commit/03928e1091e81eb020c86ed377a139dec5159d3a))
* **gene-profile:** read the label warnings from the parsed labels ([da9a1eb](https://github.com/athril/agentic-target-evidence/commit/da9a1eb9da1f93e9898f424d92193141e2934e9b))
* **gene-profile:** release a report only through five quality gates ([e8affb0](https://github.com/athril/agentic-target-evidence/commit/e8affb041217960dbd16cd60edfe320d26f4836a))
* **gene-profile:** report the label warnings of the gene's drugs ([ee7a6cc](https://github.com/athril/agentic-target-evidence/commit/ee7a6cc1c356648c63768d978fe48aeb351cb835))
* **gene-profile:** require numbers and quotes to come from the abstracts ([2d692e4](https://github.com/athril/agentic-target-evidence/commit/2d692e4e93ad5976d54c4cf86e65a7ac0b2be7c1))
* **gene-profile:** say which way each drug acts on the target ([d8d0109](https://github.com/athril/agentic-target-evidence/commit/d8d0109532b257607f46fa514c8779ee13d6893c))
* **gene-profile:** separate a source's coverage from an absence of evidence ([1e754d9](https://github.com/athril/agentic-target-evidence/commit/1e754d92280f3ef006983b85c9d5d08e22980c5b))
* **gene-profile:** show IMPC's coverage and the scope of its negative ([41ce8fd](https://github.com/athril/agentic-target-evidence/commit/41ce8fd0cd9e11c1a9b50624087421d8448582fe))
* **gene-profile:** show what the abstracts say by topic in the digest ([297e347](https://github.com/athril/agentic-target-evidence/commit/297e347ef6e44f374169efb4beb4edda5e289bc9))
* **gene-profile:** state the assumption behind each carried-over safety risk ([9f044af](https://github.com/athril/agentic-target-evidence/commit/9f044afd94d98eeb57b862c52e50fd2d5bd4a0a1))
* **gene-profile:** template 2 with source captions, empty states and term footnotes ([dd2a332](https://github.com/athril/agentic-target-evidence/commit/dd2a3320cd4bf5312acfa062a10504b9ecbc02af))
* **gene-profile:** template 3 ties prose to its evidence ([bd9ecfd](https://github.com/athril/agentic-target-evidence/commit/bd9ecfd977014e0bc44aed0253a40b418eb67664))
* **gene-profile:** the mechanism and open questions as validated writer files ([5120748](https://github.com/athril/agentic-target-evidence/commit/51207484133d51c86ac22871c6288e3d602fec05))
* **gene-profile:** the somatic pattern by the 20/20 rule ([3a7c4de](https://github.com/athril/agentic-target-evidence/commit/3a7c4de12799e48e0aedd80d536011efc8d066b7))
* **gene-profile:** type variant effects from UniProt's notes and tally them ([d222d70](https://github.com/athril/agentic-target-evidence/commit/d222d70e7eebfc4f118abb3a71bd506d69101ee3))
* **gene-profile:** variants harmonised to the allele, with phenome and spectrum ([8126aaa](https://github.com/athril/agentic-target-evidence/commit/8126aaa8062a6b6b843b05965d8ce8c263867fa8))
* **gene-profile:** warn when a block goes beyond its source ([77cdf84](https://github.com/athril/agentic-target-evidence/commit/77cdf84c01f1fc6bfaaef35d0275caa2a3802eaf))
* **gene-profile:** write Markdown only by default, PDF on request ([3766557](https://github.com/athril/agentic-target-evidence/commit/3766557af9d90c4f91cc72f65cba68660868e6e0))
* **gene-profile:** write the draft to template 2 ([6194b62](https://github.com/athril/agentic-target-evidence/commit/6194b62700dc5f4edb686808a6211007afc9f790))
* **gene-profile:** write the indication from the disease-scoped records ([dcd0e42](https://github.com/athril/agentic-target-evidence/commit/dcd0e4294647a0b28e315073f07d70e5c7570ee6))
* **genebass:** read rare-variant burden results of the UK Biobank exomes ([9fca47a](https://github.com/athril/agentic-target-evidence/commit/9fca47ad10682f04c2d987884ddf045105c40c8c))
* **genetics:** count ClinVar calls for the disease they name ([a290253](https://github.com/athril/agentic-target-evidence/commit/a2902530e65137e89cf7a34cd7082677b277652c))
* **genetics:** fetch Orphanet prevalence for the indication's own OrphaCodes ([ba48b97](https://github.com/athril/agentic-target-evidence/commit/ba48b97d86e3788082a5b8a786cafe3a99a8763c))
* **genetics:** learn each variant's effect on the target from the run's own sentences ([9b6c0d2](https://github.com/athril/agentic-target-evidence/commit/9b6c0d2914312cdd23e76a9a3c830887dbe400d6))
* **genetics:** read a variant's effect on the target from data, its risk from statements ([f5439d1](https://github.com/athril/agentic-target-evidence/commit/f5439d15ed270dd654738b17c7ff7532937a0d57))
* **genetics:** read ClinVar itself, with gnomAD's mirror as fallback ([e1e1960](https://github.com/athril/agentic-target-evidence/commit/e1e19605e349b659ef8bde44419d43e28b9ca49b))
* **genetics:** read literature direction from typed allele effects ([f738508](https://github.com/athril/agentic-target-evidence/commit/f73850825bf04104f4ac4c0e5bde4e16c7e592fa))
* **genetics:** read the direction of effect from GWAS–QTL colocalisation ([d849124](https://github.com/athril/agentic-target-evidence/commit/d84912449e2aeb7a632c32d96407d62af3879e28))
* **genetics:** set causality by rule from curated records or replicated human genetics ([c5194c8](https://github.com/athril/agentic-target-evidence/commit/c5194c84132a2c3be33d24a1dd4a5620ac6cf13c))
* **genetics:** spell out how human genetics bear on the requested direction ([5722bff](https://github.com/athril/agentic-target-evidence/commit/5722bff202052b4a8f5f5229cc627b233bbad644))
* **genetics:** take the allele's gene change from the claim's typed effect, ask only for risk ([cfb332a](https://github.com/athril/agentic-target-evidence/commit/cfb332a1d0ffd8e4c66957469477e4101bf979b1))
* **gnomad:** look up a variant's frequency by ancestry ([80ebe54](https://github.com/athril/agentic-target-evidence/commit/80ebe5430cfbf46058eaabe6e7bd8fa6b1792a1e))
* **gnomad:** name the release and transcript the constraint was computed on ([4c52da3](https://github.com/athril/agentic-target-evidence/commit/4c52da34bb838ef55b8fc3d377f7ffc9e8aaad67))
* **guards:** check lens statements of genetic direction against the shared reading ([31050cb](https://github.com/athril/agentic-target-evidence/commit/31050cb25f917e7c328b3719be3cd8c20e2fddbf))
* **guards:** ground lens prose in the target's own data and penalise flagged axes ([c36955b](https://github.com/athril/agentic-target-evidence/commit/c36955b86092781564d71f35993c085792afd157))
* **gwas_catalog:** keep the effect allele, CI and summary statistics link ([6d19b80](https://github.com/athril/agentic-target-evidence/commit/6d19b80bd80327facf127e2966d876ddaa7028ff))
* **icite:** article citation metrics from NIH iCite ([bf0d401](https://github.com/athril/agentic-target-evidence/commit/bf0d401a4e9241ac4650f6372e30be2eb790d8fb))
* **identity:** resolve the target's NCBI Gene id and the scope's MeSH ids ([d263b08](https://github.com/athril/agentic-target-evidence/commit/d263b088bf330200652a9354aec1f2f10da1af56))
* **lenses:** always show decisive evidence and report coverage per kind ([e698533](https://github.com/athril/agentic-target-evidence/commit/e69853394ec832afc7b34c39e58dec1602e48df7))
* **lenses:** budget the fixed prompt blocks and guard the context window ([8d0e339](https://github.com/athril/agentic-target-evidence/commit/8d0e33967a476cc3f4d099143a44a6fb371d8037))
* **lenses:** collapse restated findings and rank claims by relevance to the lens ([503f732](https://github.com/athril/agentic-target-evidence/commit/503f7328be2e219aa55900b6dec70ff2e78279f2))
* **lenses:** compute confidence from cited evidence; add citation and repair steps ([b7e4058](https://github.com/athril/agentic-target-evidence/commit/b7e405877c81884cf5c5398bdd59fdcdd6b24f6f))
* **lenses:** decide a paper's scope with the disease scope ([8852b8b](https://github.com/athril/agentic-target-evidence/commit/8852b8b6e3a598d198337c38c4f2e432484f2cc1))
* **lenses:** flag an axis whose rationale contradicts its value ([d5199a0](https://github.com/athril/agentic-target-evidence/commit/d5199a07da1f9eb0d3b8ba9ce777d681c478d81c))
* **lenses:** give each evidence kind its own slots in a lens's view ([367071e](https://github.com/athril/agentic-target-evidence/commit/367071ec0ef55f848cbe7f83ec8aea5dfe4aa1eb))
* **lenses:** rank a lens's literature by paper, study design and impact ([169f4c1](https://github.com/athril/agentic-target-evidence/commit/169f4c19505775f2087e690b488e966ae046be82))
* **lenses:** route drug records by their relation to the target ([38ca9df](https://github.com/athril/agentic-target-evidence/commit/38ca9dff632c65abbc627de27b1e8b939d97ff1e))
* **lenses:** run the safety lens as typed questions ([aed0a34](https://github.com/athril/agentic-target-evidence/commit/aed0a3484e95c5fd1f6c81508018e00307cc6aba))
* **lenses:** treat a secreted target's producing tissues as a site of action ([afe0135](https://github.com/athril/agentic-target-evidence/commit/afe0135be6adb976f999ed005014238024d8f674))
* **literature:** retrieve perturbation studies in the disease's animal models ([a02c856](https://github.com/athril/agentic-target-evidence/commit/a02c856cf28dc6d3acd214af817ae5fbf5ace492))
* **observability:** record what a run went without as state, not log lines ([b1d3e93](https://github.com/athril/agentic-target-evidence/commit/b1d3e932ae6d21be1a9f6c1955fed85de0d22c5b))
* **ontology:** parents of the disease's cross-referenced terms from OLS4 and MeSH ([4f34901](https://github.com/athril/agentic-target-evidence/commit/4f34901f6d3b4a762da08bebc95357b25638cd1c))
* **openfda:** fetch a drug's label by name and parse it once ([75de29b](https://github.com/athril/agentic-target-evidence/commit/75de29bca5b109845eaf900da670888b8c1f4c1d))
* **openfda:** record the request behind each FAERS count ([c760e3d](https://github.com/athril/agentic-target-evidence/commit/c760e3d7d86af89896df48aeb843d8c6c7f69b56))
* **orphanet:** serve each disorder's phenotypes, age of onset and inheritance ([2da3362](https://github.com/athril/agentic-target-evidence/commit/2da3362f3b15646f5c76b1bf5882a3933e8bbb60))
* **panel:** report designed rules apart from rescue rules ([a041a5f](https://github.com/athril/agentic-target-evidence/commit/a041a5f2fc4fd2e98072c54c56ee097c0ec9174d))
* **panel:** report what each lens was shown and which runs had unscored literature ([8f480ed](https://github.com/athril/agentic-target-evidence/commit/8f480ed51369e67f8f70b3c1118fcccc4ed0fe7b))
* **pubmed:** keep every author and the year, and label MeSH tree branches ([256c5a5](https://github.com/athril/agentic-target-evidence/commit/256c5a52cfb321c1cf0b085369d31b45cbc72aba))
* **pubmed:** keep publication types, MeSH headings and retractions of each paper ([2c8b615](https://github.com/athril/agentic-target-evidence/commit/2c8b615a36a194f1e3584d498d29d18a4ec38c44))
* **pubtator:** fetch PubTator3 entity annotations of PubMed abstracts ([2d54843](https://github.com/athril/agentic-target-evidence/commit/2d548432db95c5f1da2cc4525467a29f23bad0ab))
* **pubtator:** keep the relations PubTator3 extracts from each paper ([0a7ba26](https://github.com/athril/agentic-target-evidence/commit/0a7ba26f0f581b5b0a4922134c4f50e2e4265bbe))
* **quality:** give each paper its own iCite metrics beside journal quality ([d580b73](https://github.com/athril/agentic-target-evidence/commit/d580b7347c7172ec700ef752cfb1ec480bd46cfc))
* **regulatory:** compute the overall verdict from the lens's own axes ([e604863](https://github.com/athril/agentic-target-evidence/commit/e6048631bd8b33f0110a3c1083293ab74f75fb8f))
* **report:** show lens confidence as an evidence band ([5e2da7e](https://github.com/athril/agentic-target-evidence/commit/5e2da7ef26327ef1bfef677153a014100bd918ef))
* **report:** stamp version on top and run information at the bottom ([057aeca](https://github.com/athril/agentic-target-evidence/commit/057aeca82afcc9ca49ebcfb3e12e1625072bc3c9))
* **review:** coverage checks, prose QA and visible synthesis failures ([588f6e8](https://github.com/athril/agentic-target-evidence/commit/588f6e857a180e2f91be85700b281906a6b934c8))
* **routing:** register every reachable provider via a shared build_router ([d867072](https://github.com/athril/agentic-target-evidence/commit/d86707264cd994de52d5ce6370480a17aa76ad71))
* **routing:** run on qwen3.5:9b with thinking switched off ([b7c2b10](https://github.com/athril/agentic-target-evidence/commit/b7c2b10359addc5f2c85964e2cbfc6f90d3220f3))
* **safety:** build candidate liabilities from structured data ([dd59df4](https://github.com/athril/agentic-target-evidence/commit/dd59df4f28d24c81e906446cfe8678f74995f32d))
* **safety:** compute the verdict from the gene's findings, with one call for the prose ([473b541](https://github.com/athril/agentic-target-evidence/commit/473b541bad0444b37229603f4b64cc5ef8d5ddc6))
* **safety:** count liabilities by the direction that causes them and report an unassessed direction ([f001413](https://github.com/athril/agentic-target-evidence/commit/f001413abef6256258bf41957825cc0fcac89627))
* **safety:** treat human genetics as evidence of harm and of tolerance ([f9d4be8](https://github.com/athril/agentic-target-evidence/commit/f9d4be813cd2b17283188831961ebb024eeda5df))
* **scope:** count the disease's cross-ontology parents as related text ([0d99337](https://github.com/athril/agentic-target-evidence/commit/0d993379a6a21b4b8d405a2fa15d6c4cf427504d))
* **score:** count the direction of effect once, never as a liability ([156e9e5](https://github.com/athril/agentic-target-evidence/commit/156e9e5daea5cfa4ca4dca4bb2b3e613cfe6464e))
* **screening:** give the second pass a sectioned excerpt instead of the opening of the paper ([92f2673](https://github.com/athril/agentic-target-evidence/commit/92f2673061a59d1e86f932f9fa3fad8765e1aa2d))
* **scripts:** check the network and proxy setup before a panel run ([f168d80](https://github.com/athril/agentic-target-evidence/commit/f168d80ffbe91aaae485e0a0eebe37e0ae1505a5))
* **skills:** rename gene-dossier to gene-profile ([6d59d3b](https://github.com/athril/agentic-target-evidence/commit/6d59d3b759dcdfa87ee106a8803c2105d3c46429))
* **skills:** ship gene-dossier as a Claude Code plugin ([8ccf7d1](https://github.com/athril/agentic-target-evidence/commit/8ccf7d1253ed442d3fa6f62296479b8cf44a0eb6))
* **statements:** cap the sentences read per run, not only per paper ([10f3dbd](https://github.com/athril/agentic-target-evidence/commit/10f3dbd9374fb40bcd0707193b7f477f2a191dd5))
* **tissue:** infer disease-relevant tissues from the disease name and therapeutic area ([7b7a89b](https://github.com/athril/agentic-target-evidence/commit/7b7a89b1f9ee6954f6361522d954909144e6facc))
* **tokens:** count prompt tokens with the model's measured ratio ([6527909](https://github.com/athril/agentic-target-evidence/commit/65279097830cb73452d196490b71171a4af6118d))
* **trials:** check trial figures and outcomes against the trial's own sources ([b218838](https://github.com/athril/agentic-target-evidence/commit/b218838b2aaa0ee1816bb5c47c1b332e60344704))
* **workflow:** wire liabilities, scoped evidence and trial facts into the graph ([11b80bf](https://github.com/athril/agentic-target-evidence/commit/11b80bfd1b28bf32fb74fe0c42c22444fa43859f))


### Bug Fixes

* **biology:** leave essentiality to the safety lens and set the verdict from the axes ([14ef386](https://github.com/athril/agentic-target-evidence/commit/14ef3861c1f30eed0eae48281cf6e6f595f0a4d5))
* **cache:** key lens verdicts on a digest of their inputs ([7107807](https://github.com/athril/agentic-target-evidence/commit/7107807dea0e8ddaf228d89fc74a9f72cd744c46))
* **cache:** key source-quality and lens caches on each step's model ([8654c0b](https://github.com/athril/agentic-target-evidence/commit/8654c0be205cb99b181261d0fa8eecb88ba1feae))
* **cache:** skip stale evidence rows instead of discarding the whole hit ([5e2c4fe](https://github.com/athril/agentic-target-evidence/commit/5e2c4febd6d13d42a3b4a13c55742cce6360f20d))
* **cache:** treat stale cached lens verdicts as a cache miss ([a83c5ef](https://github.com/athril/agentic-target-evidence/commit/a83c5ef2ebfd93832677a98f2c36900a5aa7b014))
* **checkpoint:** run the checkpointer on a validated connection pool ([43b1199](https://github.com/athril/agentic-target-evidence/commit/43b11996d514de3a83ab637ea9df8c32f6b98c1b))
* **chembl:** report the bioactivity count as a floor when its query fails ([dbc603a](https://github.com/athril/agentic-target-evidence/commit/dbc603ae0bbf91a61385719be1540f92bd5aeebb))
* **claims:** ground extracted claims in their own source ([78f6278](https://github.com/athril/agentic-target-evidence/commit/78f6278538ede23646dec0a2623fc1249cd77863))
* **clingen:** read gene validity from the published bulk file ([3d26e19](https://github.com/athril/agentic-target-evidence/commit/3d26e19a354312bc0a34f4a3642241c21a98d0f2))
* **clingen:** read the region dosage curation, not only the gene list ([f0d7866](https://github.com/athril/agentic-target-evidence/commit/f0d7866e999251488ff501732c592ed124f53d3e))
* **clinical:** arm the drug-approval guard with the target's known drugs ([8304875](https://github.com/athril/agentic-target-evidence/commit/83048750b96ae9dbcc1ccdbe5c316834a0b2bf4b))
* **cli:** point make run and docs at src/run_analysis.py ([a8afed2](https://github.com/athril/agentic-target-evidence/commit/a8afed298eb208342178a9c41b86589f22f57f44))
* **commercial:** flag patent encumbrance asserted from patent titles ([1010086](https://github.com/athril/agentic-target-evidence/commit/10100864b28db5e4066985fc60d24d80b5f4a755))
* **competition:** search the disease's other names when its own matches no label ([875f9cc](https://github.com/athril/agentic-target-evidence/commit/875f9cc61d2af3a783c0c6cc6d37d2f968531501))
* **confidence:** a corroborating axis discounts a lens but never caps it ([bd96d91](https://github.com/athril/agentic-target-evidence/commit/bd96d9168a84a4ff0397f008401452bee682cb11))
* **config:** map Open Targets' current therapeutic-area ids to disease classes ([2e1dca1](https://github.com/athril/agentic-target-evidence/commit/2e1dca13dc64cc403cf757d8afd4acf376126767))
* **constraint:** band the metrics only where the data support a band ([186358f](https://github.com/athril/agentic-target-evidence/commit/186358fab6f41bf6d6f020c190e6482b7f193694))
* **constraint:** read constraint as population depletion ([70f5ebf](https://github.com/athril/agentic-target-evidence/commit/70f5ebf5ef4606953f287d16f55463b0ebee3acc))
* **depmap:** fall back to the figshare release when the portal returns a bot-check page ([b4dfe35](https://github.com/athril/agentic-target-evidence/commit/b4dfe359948581ea3fb1cd86a9c6b61b69d5e4c6))
* **deps:** upgrade 17 packages to clear 85 dependabot alerts ([91ac5a3](https://github.com/athril/agentic-target-evidence/commit/91ac5a37205b00ea5ddeb787f7a9822b54115b7f))
* **direction:** count a label warning only when it governs the indication ([bea38b3](https://github.com/athril/agentic-target-evidence/commit/bea38b3edef4f7e85b5150524ffa310e7d95ed98))
* **direction:** read label warnings with the disease scope ([e824d4c](https://github.com/athril/agentic-target-evidence/commit/e824d4ceb9d362acd6869df9f1a6bb40c4b91795))
* **direction:** scope literature direction claims with the disease scope ([862e361](https://github.com/athril/agentic-target-evidence/commit/862e3614ebd8472b4f31fe69563c2321167c6787))
* **direction:** scope literature direction evidence to the target and disease, and net out its noise ([62b8604](https://github.com/athril/agentic-target-evidence/commit/62b86044bd89215453cef2b19fe5b799f6c36086))
* **expression-atlas:** raise the client timeout to 120 s ([3c1035d](https://github.com/athril/agentic-target-evidence/commit/3c1035d2154a78a34b84aa24e2f9272e9a0b9109))
* **extraction:** extract claim directions without showing the run's direction ([2c856e8](https://github.com/athril/agentic-target-evidence/commit/2c856e89f8e4615f23c00bce9cdd22d58217cf1b))
* **extraction:** record faithfulness verdicts as flags and stop counting literature harms ([6871634](https://github.com/athril/agentic-target-evidence/commit/6871634a12326d0c9045136fed10abf30bca99e6))
* **faers:** report serious and fatal outcomes as shares of reports ([c1d027f](https://github.com/athril/agentic-target-evidence/commit/c1d027f8bc310be8e6132cc552f81571184d9a7f))
* **gene-profile:** a label must say the drug acts on the gene ([f3f5e8f](https://github.com/athril/agentic-target-evidence/commit/f3f5e8f4e59d15b7c95b0c5e0083f73a363a6a10))
* **gene-profile:** an anchor fails when its format or unit does not apply ([a06ae45](https://github.com/athril/agentic-target-evidence/commit/a06ae4568e1ffc732d7126a64ce65c809417fc67))
* **gene-profile:** declining to conclude from a screen is not a conclusion ([40e90d3](https://github.com/athril/agentic-target-evidence/commit/40e90d3c696958832ad833b5521650832df6b98d))
* **gene-profile:** drop the sections render.py writes from the draft ([bc2da80](https://github.com/athril/agentic-target-evidence/commit/bc2da80d070747241f67a9531e57c26601d657ab))
* **gene-profile:** give each recorded inheritance mode its own prevalence row ([02d55a9](https://github.com/athril/agentic-target-evidence/commit/02d55a93ab3de6151c8a870b9abc85cce03a8ae8))
* **gene-profile:** keep figure text legible and apart ([71fda82](https://github.com/athril/agentic-target-evidence/commit/71fda822bd2c22464cc7cea67c51274b2de55c48))
* **gene-profile:** keep registered papers through a re-fetch ([7a12735](https://github.com/athril/agentic-target-evidence/commit/7a127357974ebab6b522388d9c5da23291723f7f))
* **gene-profile:** link exact records and requests, never blank searches ([d24e448](https://github.com/athril/agentic-target-evidence/commit/d24e4483e13000fa62392d72f84a2d4c75c86402))
* **gene-profile:** mark gated-off sources not checked in the matrix ([362b54c](https://github.com/athril/agentic-target-evidence/commit/362b54c5564c97d5b920c908a0da0316e870e89a))
* **gene-profile:** match salt-named labels to the drug table ([2f735d6](https://github.com/athril/agentic-target-evidence/commit/2f735d6731381bad9f61081fc3f955639b2407a5))
* **gene-profile:** name the design behind each How-we-know cell ([9292f00](https://github.com/athril/agentic-target-evidence/commit/9292f00aae2f01ad84b1ee777663e669c33b3eb4))
* **gene-profile:** quote the flagged sentence in each gate note ([0f3f95e](https://github.com/athril/agentic-target-evidence/commit/0f3f95e7187f83e911bfb4c629ad2102d6f3c10c))
* **gene-profile:** record the totals the FAERS table is cut from ([af4139f](https://github.com/athril/agentic-target-evidence/commit/af4139ffd3a345ca6596a232dcb6c4b783eda3c6))
* **gene-profile:** refuse a script defect resolved as a review limitation ([45ae0f5](https://github.com/athril/agentic-target-evidence/commit/45ae0f5adca4c24f31fb77c0cdaa058e1af892b9))
* **gene-profile:** report licence-gated GBD as not checked ([0be9adc](https://github.com/athril/agentic-target-evidence/commit/0be9adcf5bcac5912c67492b9abb073c440407fb))
* **gene-profile:** report what the records show in the tables ([c9878d1](https://github.com/athril/agentic-target-evidence/commit/c9878d187f351b76a53cc31277459e4230a61189))
* **gene-profile:** retry failed gnomAD lookups and say when one stays failed ([65607a5](https://github.com/athril/agentic-target-evidence/commit/65607a5e2cb6b92774f758b9e2d72251214e078a))
* **gene-profile:** say only what an empty FAERS lookup shows ([466081d](https://github.com/athril/agentic-target-evidence/commit/466081d152c4112d955c0ce51a2982635aed4d46))
* **gene-profile:** say which pLoF variants a homozygote count covers ([3051194](https://github.com/athril/agentic-target-evidence/commit/3051194ac30c65678b2e1477d591720a0cdef59e))
* **gene-profile:** tell the auditor which records a block also cites ([e2ccca8](https://github.com/athril/agentic-target-evidence/commit/e2ccca8183030c2e4126607b306ad26579e9d7a4))
* **genetics:** compute the genetics verdict from its axes ([0543521](https://github.com/athril/agentic-target-evidence/commit/0543521e8c94416a0d913fb39a54fc12806dd08a))
* **genetics:** read a related disorder's Mendelian mechanism as its own, not the indication's ([3899313](https://github.com/athril/agentic-target-evidence/commit/3899313cb5e254261eb4edcb7a6533d7b830b252))
* **genetics:** read homozygous loss-of-function carriers into the constraint reading ([2d334c8](https://github.com/athril/agentic-target-evidence/commit/2d334c87af2eeffd8c33f23af8478e49de7c5642))
* **genetics:** refetch cached genetics evidence for the indication's Orphanet prevalence ([86b47fa](https://github.com/athril/agentic-target-evidence/commit/86b47fa69498e53c55113bc77658db87206b6a64))
* **genetics:** refetch cached genetics retrieved before the full GWAS scan ([ea908af](https://github.com/athril/agentic-target-evidence/commit/ea908afb3f2e42a24db66729f015528bc36e5274))
* **genetics:** scope Mendelian evidence to the run's disease ([d304744](https://github.com/athril/agentic-target-evidence/commit/d304744c7c0b8b11d253cb66039c1dedfd62ab15))
* **genetics:** steer GoF genes away from the "missense-intolerant" trope ([429cb0f](https://github.com/athril/agentic-target-evidence/commit/429cb0f96322c94decec2bbc2ebca59090927160))
* **genetics:** stop a cached Open Targets row from skipping the genetics agent ([fa7a8f3](https://github.com/athril/agentic-target-evidence/commit/fa7a8f3992c056c47c354f073cd1f71911adb6c8))
* **genetics:** support only when the direction of effect is not contradicted ([d8a5d56](https://github.com/athril/agentic-target-evidence/commit/d8a5d56eef1f8b0c1280734d4ac53727c9abc8bd))
* **genetics:** type variants by their consequence and never read a splice variant's QTL ([75275b9](https://github.com/athril/agentic-target-evidence/commit/75275b929c2b83fa2ec4fa7ca5a5d7398e5fc9b7))
* **gnomad:** count exomes and genomes together for pLoF variants ([989fc64](https://github.com/athril/agentic-target-evidence/commit/989fc6459890759d824a1074597d67400c56f2d2))
* **gnomad:** do not infer tolerance from homozygous pLoF genotypes ([98fd639](https://github.com/athril/agentic-target-evidence/commit/98fd639a0f0abf3d53efd4faf94cfc20aaed7b5e))
* **grounding:** place a disease mention in scope by its names when its MeSH id is not ([8e5ba91](https://github.com/athril/agentic-target-evidence/commit/8e5ba9152f600c0aeb536ccea1d1efd8fe30c5db))
* **grounding:** take a variant's carried allele from its own notation ([72d0f7e](https://github.com/athril/agentic-target-evidence/commit/72d0f7ece4f006470286da5a3301a00b2de49a30))
* **guards:** flag a denied boxed warning only for a drug acting on the target ([b5ae58e](https://github.com/athril/agentic-target-evidence/commit/b5ae58e9093f5f026f7dfa0406a1185569cb12c4))
* **guards:** flag figures from sources the run never had and non-oncology slips ([6ae8a37](https://github.com/athril/agentic-target-evidence/commit/6ae8a377d8111c760a54355f5ab3346d0a28e94c))
* **guards:** ground a figure quoted from a table headed by its source ([aa1f9cd](https://github.com/athril/agentic-target-evidence/commit/aa1f9cd76c45b4009883612d8e3c07d1c55690b7))
* **guards:** read powers of ten and trial phases as no source figure ([4cfc4af](https://github.com/athril/agentic-target-evidence/commit/4cfc4af68072a618cb223607c415d4dc4c1bc11c))
* **guards:** say why the phenotype-omission check did not run ([f286ac2](https://github.com/athril/agentic-target-evidence/commit/f286ac26e2f263f6db73d90725182e17b2942bf6))
* **guards:** scope the off-target boxed-warning check to the drug named ([515604d](https://github.com/athril/agentic-target-evidence/commit/515604d012079f45e4e34b67692ba0d494e9a442))
* **guards:** skip haploinsufficiency mentions the sentence dismisses ([78169b1](https://github.com/athril/agentic-target-evidence/commit/78169b10c47bb1352e4a27520eedf8b046b935e4))
* **gwas:** scan every significant association when the query is scoped to a disease ([762022c](https://github.com/athril/agentic-target-evidence/commit/762022c699ecd8525991c32111bfafd0a265ca29))
* **http:** honour Retry-After and rate limits in the shared client and connectors ([5061599](https://github.com/athril/agentic-target-evidence/commit/506159942bc5535fd33f2f2fb7da7d7ed93765c4))
* **http:** retry a request that failed to connect ([063b022](https://github.com/athril/agentic-target-evidence/commit/063b0223ef0d38d1ae6f589dca850f9ee7d0ca0d))
* **icite:** send at most 200 PMIDs per request ([69f503b](https://github.com/athril/agentic-target-evidence/commit/69f503b16f19209d9b2641ee462c2c90e374963c))
* **impc:** match the mouse symbol so knockout phenotypes are found ([365dbdd](https://github.com/athril/agentic-target-evidence/commit/365dbdd2638b1b4c3019c3a2bbe99b26909fddee))
* **impc:** tell "never phenotyped" apart from "no phenotype" ([c9334eb](https://github.com/athril/agentic-target-evidence/commit/c9334eb3e740bd4d1e76f0f6d9a2cb0fde9e1bcf))
* **investigator:** reach a local MCP gateway without the environment's proxy ([46cf3b7](https://github.com/athril/agentic-target-evidence/commit/46cf3b7387b7e893b9d76c2454cc32efc24f7085))
* **lenses:** a tested-in-the-clinic axis never counts against an oppose ([24868c1](https://github.com/athril/agentic-target-evidence/commit/24868c12448e704b178c58d8bca2ee4cf5b65e56))
* **lenses:** align prose that states a verdict the rules then changed ([07ab0eb](https://github.com/athril/agentic-target-evidence/commit/07ab0ebf4f5f85da4f46c03887daa39ec28f6227))
* **lenses:** apply the biology, safety, genetics and commercial rules in code ([057343f](https://github.com/athril/agentic-target-evidence/commit/057343f194db017652165b00748965bec024663f))
* **lenses:** ask once for valid JSON when a lens verdict cannot be parsed ([4f28178](https://github.com/athril/agentic-target-evidence/commit/4f28178d691ff9dc3e938f62bf6661122fb92469))
* **lenses:** bound the reply length of a question answered once per claim ([d0c86f6](https://github.com/athril/agentic-target-evidence/commit/d0c86f6ef5341eb7df9bccc3d9ed040f2d8a0791))
* **lenses:** calibrate strength for direction opposes and unsettled safety support ([01f8b35](https://github.com/athril/agentic-target-evidence/commit/01f8b35b544c80b48c1c74c7e772db8ea15be67b))
* **lenses:** cut model deliberation leaked into verdict prose ([91dcf00](https://github.com/athril/agentic-target-evidence/commit/91dcf00e16d721fccf2c53e6a93df250c5cd7110))
* **lenses:** de-specialise lens prompts and tighten per-lens rules ([0b53e9d](https://github.com/athril/agentic-target-evidence/commit/0b53e9d72de397ffe7176ebbf6d8e9cd14f96fbd))
* **lenses:** fit lens prompts into the model's context window ([03a7e4f](https://github.com/athril/agentic-target-evidence/commit/03a7e4fc2f45724393431364419150209ef8c6e9))
* **lenses:** key cached lens verdicts on the version of the lens rules ([3280213](https://github.com/athril/agentic-target-evidence/commit/32802135b21d6959ecfad8260f256e9f5fde9775))
* **lenses:** make output guards clause-aware and report unevaluated checks ([b44d8fa](https://github.com/athril/agentic-target-evidence/commit/b44d8fafc5c79d4de4919895703c9717fa62709f))
* **lenses:** record a corrected sentence whole, not cut at 300 characters ([b19e22b](https://github.com/athril/agentic-target-evidence/commit/b19e22b6f8d9620767c3253e5cf8b543588e455f))
* **lenses:** refresh what a verdict derived when a rule replaces it ([838106d](https://github.com/athril/agentic-target-evidence/commit/838106dadaaaf9baa4908257b10a3ad0bd9ea7b5))
* **lenses:** show a lens the most informative trials first ([3d22594](https://github.com/athril/agentic-target-evidence/commit/3d22594a14c02787f7e53602377d177d5b8da030))
* **lenses:** stop relevance and quantity guards flagging compliant prose ([09d9dda](https://github.com/athril/agentic-target-evidence/commit/09d9dda52535c407b2965c8486491258394213c2))
* **lenses:** treat a secreted target's producing tissues as where it acts ([ca57293](https://github.com/athril/agentic-target-evidence/commit/ca57293b1bbc57ae481cf6ec4dacdc009b2fd567))
* **links:** point Project Score and TTD at real records, label by source ([83e70cb](https://github.com/athril/agentic-target-evidence/commit/83e70cbd7a1d75964abad1b4bef4d7ae20a172dc))
* **literature:** cache a disease with no model phenotype, never a failed lookup ([dd0a28f](https://github.com/athril/agentic-target-evidence/commit/dd0a28f047d6d5d664f801e356e2ad5a3031c542))
* **llm:** recover common JSON slips and seed Ollama sampling ([dbc8342](https://github.com/athril/agentic-target-evidence/commit/dbc8342dc03dee2a0cc98e2cb43c21c6fcbaee58))
* **observability:** tell a clean run from one that predates the degradation ledger ([e78c4fb](https://github.com/athril/agentic-target-evidence/commit/e78c4fb6de7b78368aa7e3adafdd3d840f92cb29))
* **ontology:** a failed Monarch request is not an empty phenotype set ([46a4836](https://github.com/athril/agentic-target-evidence/commit/46a48366771f2ef63656b0b3cb9349291f7fa62a))
* **ontology:** request OLS parents over https instead of following the returned link ([a1d786a](https://github.com/athril/agentic-target-evidence/commit/a1d786abe1067b94729422f81ac2a20cd460bb92))
* **openfda:** fetch the target's drug labels by name and keep only their liabilities ([5abde35](https://github.com/athril/agentic-target-evidence/commit/5abde351751b774df9c965e449ed8f53d5d51751))
* **openfda:** find FAERS reports filed under product names ([6937b78](https://github.com/athril/agentic-target-evidence/commit/6937b78db325e22747cc2b6d7735181a6e1e7ef9))
* **openfda:** keep a drug's own label over a brand-listed combination ([c290c10](https://github.com/athril/agentic-target-evidence/commit/c290c104107b7846921d954cafbc053b68d2a722))
* **openfda:** name the labels openFDA has not harmonised from their own text ([68bab02](https://github.com/athril/agentic-target-evidence/commit/68bab028b6b826d3defd61b539ba1e1a40b2c03b))
* **openfda:** read boxed warning titles joined by lower-case words ([a227604](https://github.com/athril/agentic-target-evidence/commit/a2276041dc93c184d317a7a42339ca4e515d2b62))
* **openfda:** search labels by the disease's synonyms and drop diagnostic labels ([c65b81f](https://github.com/athril/agentic-target-evidence/commit/c65b81f3f2a45fc0ff0b35beddd2c0d7e5282455))
* **opentargets:** give each known-drug indication its own stage and approval ([d2380e7](https://github.com/athril/agentic-target-evidence/commit/d2380e78ea001ef027acab40e615eab25eb5581b))
* **panel:** read DATABASE_URL from .env when scoring a panel ([215c8be](https://github.com/athril/agentic-target-evidence/commit/215c8be61ccd89bfdbdee5ad44beef611cb798bd))
* **panel:** report the lenses' own signal beside the gated band ([e95b21d](https://github.com/athril/agentic-target-evidence/commit/e95b21dc85e03a9a1ed8274a1d88fe4752712692))
* **persistence:** chunk evidence upserts under the driver's parameter limit ([64420b5](https://github.com/athril/agentic-target-evidence/commit/64420b52b60b90649554d92e4d731baf2018965d))
* **pubmed:** a malformed full-text record costs that record only ([78ec582](https://github.com/athril/agentic-target-evidence/commit/78ec582cf6e793be83971c21fdf27b01d57e874e))
* **pubmed:** resolve PMC ids from the pubmed_pmc elink so full text is fetched ([fce95a8](https://github.com/athril/agentic-target-evidence/commit/fce95a8cf61e2ac668a82bd81ceaf4e4d997358b))
* **pubtator:** split a rejected batch so one bad PMID costs only itself ([0ca6c46](https://github.com/athril/agentic-target-evidence/commit/0ca6c46501de7bcb63002e0693928a6f29e5d25d))
* **quality:** reuse cached source quality on a repeat run of the same pair ([7dbcda8](https://github.com/athril/agentic-target-evidence/commit/7dbcda8be28234653832b4818ecb9441d3b43209))
* **regulatory:** say neutral, not insufficient, when label data exist ([7fbc573](https://github.com/athril/agentic-target-evidence/commit/7fbc573a3b518aa65af50cdb81cf73c28f9db1c9))
* **regulatory:** state each label's direction and any warning about the indication ([e7d483f](https://github.com/athril/agentic-target-evidence/commit/e7d483f250b7a7ea527425de1e795fba462a6658))
* **repair:** let a repair delete or soften a sentence, never add a fact ([4834713](https://github.com/athril/agentic-target-evidence/commit/483471338b7eae1aae8b6fb791fc7aeed222f881))
* **repair:** reject a rewrite that brings in a missing-value placeholder ([b75620e](https://github.com/athril/agentic-target-evidence/commit/b75620ebee29af4c6ba8844a2cd9c879355fb4d8))
* **report:** list cached evidence in the full report and summary CSV ([62527e6](https://github.com/athril/agentic-target-evidence/commit/62527e6bae1712b035adbfdd825160d66f9c2c58))
* **report:** name each finding's source and drop a placeholder model confidence ([879b973](https://github.com/athril/agentic-target-evidence/commit/879b973e77ac4aaa5c0625d646913e7f9168e5d8))
* **report:** print each guard note once instead of in every summary copy ([efb254b](https://github.com/athril/agentic-target-evidence/commit/efb254b17960c1c44409834524a6f9c9f2819f83))
* **retrieval:** add target-level literature queries and re-screen unanswered items ([82583ec](https://github.com/athril/agentic-target-evidence/commit/82583ec35629011bcefb32043e159a772b6690d4))
* **routing:** honour task-keyed agent_models entries ([057aeca](https://github.com/athril/agentic-target-evidence/commit/057aeca82afcc9ca49ebcfb3e12e1625072bc3c9))
* **safety:** a favourable toxicity axis reached by absence counts as a non-finding ([e5c6f1a](https://github.com/athril/agentic-target-evidence/commit/e5c6f1a40e5c547fd88ca7322d033f8aa0a670a0))
* **safety:** apply the verdict rules before the split lens writes its narrative ([56e898d](https://github.com/athril/agentic-target-evidence/commit/56e898d4e6bb3336ba6f83e9e720b89f0d927796))
* **safety:** rate death under an experimental challenge as a medium liability ([3d8d75b](https://github.com/athril/agentic-target-evidence/commit/3d8d75b6a31cbaea18ef2a754306cd45ee3d264a))
* **safety:** read curated events' direction and count them once per change ([870dd3f](https://github.com/athril/agentic-target-evidence/commit/870dd3f932d4c030f6e08ad026534d8d04abc2f7))
* **safety:** round constraint metrics in the liability evidence ([aec0714](https://github.com/athril/agentic-target-evidence/commit/aec07141224040492222956bd86f906dfc9295a3))
* **safety:** settle findings against every approved label, combinations included ([900ae87](https://github.com/athril/agentic-target-evidence/commit/900ae871c7462250d78e7b6e7539d9d7f63cd3cf))
* **safety:** settle liabilities against the labels of drugs approved in the indication ([683c6a4](https://github.com/athril/agentic-target-evidence/commit/683c6a4f11731dd520b988161cd41f9d9221f7c3))
* **score:** subtract only liabilities the safety lens left open ([91348f0](https://github.com/athril/agentic-target-evidence/commit/91348f080e32df82cae59823c4e68b2d03dcd5b8))
* **scoring:** gate the score on the direction of effect and lead with the decision ([f4b3165](https://github.com/athril/agentic-target-evidence/commit/f4b3165c2fbc42cf29cce743e6e61a35142fcb68))
* **screening:** apply the phased-trial keep past the screening cache ([ba20f78](https://github.com/athril/agentic-target-evidence/commit/ba20f78127fde1c58d5b59eb9c4abbfbc218110e))
* **screening:** keep phased drug trials that name the target gene ([7af089b](https://github.com/athril/agentic-target-evidence/commit/7af089b6e60a6ab65a7b7fd5d752b309bb33968a))
* **screening:** keep the verdicts of a truncated reply and size it for the model ([69d1f6d](https://github.com/athril/agentic-target-evidence/commit/69d1f6ddc830752ed71b7b8346a269fa26056018))
* **screening:** let openFDA labels and FAERS reach the lenses ([13a0d03](https://github.com/athril/agentic-target-evidence/commit/13a0d03435ff6bf629e7a7f86d8179295f8bc87a))
* **screening:** let the second pass re-judge uncertain items on full text ([f1af6c9](https://github.com/athril/agentic-target-evidence/commit/f1af6c928564a1d40f8b258fc651f43b7e478e7a))
* **scripts:** check only https in the network preflight ([51abcc8](https://github.com/athril/agentic-target-evidence/commit/51abcc839b67846b9a5e6368432afb7438f8123f))
* **statements:** a reported null result has no outcome direction ([8a8ce92](https://github.com/athril/agentic-target-evidence/commit/8a8ce928197e8d9465394d40af75d0f11f49d47d))
* **statements:** give no outcome direction to a sentence that states none ([37f2273](https://github.com/athril/agentic-target-evidence/commit/37f2273a7b313feba5a04938b08ac6f15afb3c35))
* **tokens:** learn the characters-per-token ratio per task ([75a6a17](https://github.com/athril/agentic-target-evidence/commit/75a6a1702dce3854ca7dea50eb3bc7998e007a3f))
* **trials:** read a registry condition written as an inverted MeSH heading ([a6258e1](https://github.com/athril/agentic-target-evidence/commit/a6258e1e023511ede044e14da69affc62b2e47c3))


### Performance Improvements

* **genetics:** ask the allele question about three batches, indication first ([7b217cf](https://github.com/athril/agentic-target-evidence/commit/7b217cfdef01618340dff38d7f053c3f629a85a3))


### Documentation

* **components:** describe build_router provider registration ([e0d8ecc](https://github.com/athril/agentic-target-evidence/commit/e0d8ecc08a53d8d9bc9c7c08a7c0256a54159edd))
* describe the pipeline without gap detection or a replan loop ([bbf190c](https://github.com/athril/agentic-target-evidence/commit/bbf190c634f54cfaa06c553f0a67f2f566845ecd))
* document gene-profile plugin and recent pipeline fixes ([39d558f](https://github.com/athril/agentic-target-evidence/commit/39d558fb8f24340579f6bcf387c83a2f41ccc11f))
* **examples:** refresh the example dossier from a current TRPC6 run ([1721227](https://github.com/athril/agentic-target-evidence/commit/1721227a344b3af80b65a3c2dbf286ebecd40d23))
* **gene-profile:** a block no reader could judge stays without a verdict ([9e3b0d3](https://github.com/athril/agentic-target-evidence/commit/9e3b0d38a13c519222ef5f223e25e81608f1f058))
* **gene-profile:** describe template 4, the review step and release gates ([6ea89cf](https://github.com/athril/agentic-target-evidence/commit/6ea89cf826dea1cb4d84baacfe08ee402a6bd15f))
* **gene-profile:** researcher-first template, writing rules and evals ([f37c61f](https://github.com/athril/agentic-target-evidence/commit/f37c61f8df3474355d21557964b1b9e0875532e6))
* **lenses:** describe how the genetics verdict follows the direction reading ([46dbceb](https://github.com/athril/agentic-target-evidence/commit/46dbceb524741b0bf533f429ec0758a561143cf3))
* **lenses:** explain how lens confidence is computed and checked ([a67cd1d](https://github.com/athril/agentic-target-evidence/commit/a67cd1dbecb51c16addad96246cc9e8d01dba1ce))
* **notice:** credit iCite citation metrics ([e09d499](https://github.com/athril/agentic-target-evidence/commit/e09d49920dbf992365ba14965a6703e087470839))
* record the gene profile's new sources, keys and licences ([8d34a0e](https://github.com/athril/agentic-target-evidence/commit/8d34a0e45015f57a7e30360650fd6fd0a900d500))
* **report:** align provenance docstring with explicit-assignment routing ([d807d8e](https://github.com/athril/agentic-target-evidence/commit/d807d8ef4e57895de181dde58d00a79d4f1022f8))


### Continuous Integration

* treat an already-published registry version as done ([888a068](https://github.com/athril/agentic-target-evidence/commit/888a068f35055f82a9de7daf90f3072e525da149))

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
