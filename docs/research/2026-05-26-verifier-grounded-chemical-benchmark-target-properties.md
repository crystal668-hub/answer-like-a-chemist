# Verifier-grounded open-generation chemical benchmark 目标性质调研报告

日期：2026-05-26  
范围：本报告只做目标性质调研与 benchmark 设计建议，不实现 runner，也不生成正式题目集。

## 0. 结论摘要

适合作为 verifier-grounded open-generation benchmark 的性质，核心不是“科学上是否重要”一个维度，而是同时满足四个条件：

1. 候选对象可以开放生成，例如 SMILES、CIF、pymatgen/ASE 结构、催化剂 slab+adsorbate、反应 SMILES 或反应条件。
2. 性质可以用数值、概率、类别概率或可排序 score 表达。
3. verifier 可以独立运行、冻结版本，或使用冻结数据库快照，不依赖人工判断。
4. 任务能区分会使用化学工具/数据库/模型的 agent 与只靠语言模式猜测的 LLM。

首版最稳的方向是“小分子 RDKit/ADMET + 材料 band gap/stability + 低成本量子性质 + 少量反应/催化验证”。最不建议首版就重投的是真实反应条件优化、通用选择性、通用热导/离子电导、真实反应势垒，因为这些要么输入规范很难，要么 verifier 成本高，要么模型适用域窄。

## 1. 推荐性质总览表

评级说明：

- 工程可行性：A = 可本地批量跑、失败少；B = 可做但需冻结模型/数据与输入规范；C = 可研究但首版风险较高；D = 暂不建议进入首版。
- 首版优先级：P0 = 首版核心；P1 = 首版可选/第二批；P2 = 研究储备；P3 = 暂缓。

| 方向 | 候选目标性质 | 候选输入 | verifier 示例 | 输出形式 | 推荐评分 | 工程可行性 | 首版优先级 |
|---|---|---|---|---|---|---|---|
| 小分子 | RDKit 药物样性质：QED、logP、TPSA、MW、HBD/HBA、rotatable bonds | SMILES/InChI | RDKit QED/Crippen/Descriptors；PubChem PUG-REST 可作标签辅助 | 连续值/规则计数 | hard validity + 窗口约束 + 距离惩罚；多目标可用 weighted desirability | A | P0 |
| 小分子 | SA score / 合成可及性 | SMILES | RDKit Contrib `SA_Score` | 1-10 左右连续 score | minimize 或上限约束；与 QED/logP 做 hard constraint + soft objective | A | P0 |
| 小分子 | 水溶解度 logS / AqSolDB-style solubility | SMILES | SolTranNet；ADMET-AI；OPERA WS；MoleculeNet/ESOL 标签 | log mol/L 或模型连续值 | 目标窗口或 maximize/minimize；对盐/金属配合物设适用域过滤 | A/B | P0 |
| 小分子 | logD / pKa | SMILES | OPERA pKa/logD；ADMET-AI physicochemical；商用/外部模型可后续替换 | 连续值 | 生理 pH 下窗口约束；多酸碱中心需定义取 major microspecies 或 strongest acidic/basic pKa | B | P1 |
| 小分子 | hERG、AMES、DILI、LD50、ClinTox/Tox21 | SMILES | ADMET-AI；OPERA/CATMoS/CERAPP/CoMPARA；TDC/MoleculeNet 标签 | 概率、分类、LD50 连续值 | toxicity probability minimize；hard safety threshold + soft potency/drug-likeness | B | P0/P1 |
| 小分子 | CYP inhibition/substrate、BBB、Caco-2、HIA、P-gp、PPB、clearance、VDss | SMILES | ADMET-AI；TDC ADMET group；OPERA Caco-2/FuB/Clint | 概率或连续 ADMET 值 | endpoint-specific maximize/minimize/window；多 endpoint Pareto/desirability | B | P1 |
| 小分子/药化 | 靶点活性 / binding affinity proxy | SMILES + target id/protein sequence | ChEMBL pChEMBL 冻结标签；DeepPurpose/DeepDTA；TDC DTI | pChEMBL、pKd/pKi、probability | maximize pChEMBL / minimize predicted Kd；hard ADMET constraints | B/C | P1 |
| 材料 | band gap / metal-nonmetal | CIF/pymatgen Structure/composition | Materials Project labels；Matbench `mp_gap`/`expt_gap`；MEGNet/MatGL/Matformer/CHGNet/MODNet | eV 或 class | 目标 band-gap window，例如 1.1-1.8 eV；distance-to-window | A/B | P0 |
| 材料 | formation energy、energy above hull、stability proxy | CIF/Structure/composition | Materials Project labels/API；pymatgen PhaseDiagram；CHGNet/M3GNet/MatGL/MACE/MatterSim relaxation+energy | eV/atom | minimize E_hull；stable threshold <= 0.05-0.1 eV/atom + objective | B | P0 |
| 材料 | elastic modulus：bulk/shear K/G、Poisson ratio | CIF/Structure | Materials Project elastic labels；Matbench `log_kvrh`/`log_gvrh`；CHGNet/atomate2 elastic workflow | GPa/log10(GPa) | maximize/minimize/window；hard stability constraint | B/C | P1 |
| 材料 | dielectric constant / refractive index | CIF/Structure | Materials Project dielectric fields；Matbench `dielectric` target `n`；MODNet pretrained refractive index | unitless | maximize dielectric/refractive index under stability/band-gap constraints | B | P1 |
| 材料 | density / volume / composition constraints | CIF/Structure | pymatgen Structure；Materials Project summary | g/cm3, A3/atom | target window；作为 hard plausibility constraint | A | P0 auxiliary |
| 材料 | thermal conductivity / ionic conductivity | CIF/Structure/composition | Matminer/JARVIS/专用文献模型；MD/phonon workflow | W/mK, S/cm | maximize/minimize；需要 narrow domain | C/D | P2/P3 |
| 催化/表面 | adsorption energy / binding energy | ASE slab+adsorbate 或 graph | Open Catalyst OC20/OC22 labels；FAIR-Chem UMA；GemNet-OC/EquiformerV2；ASE relax | eV | target descriptor window；relative adsorption energy difference | B/C | P1 |
| 催化/表面 | HER/ORR/CO2RR/NRR volcano descriptors | slab+intermediate set | OC/UMA/DFT surrogate；pymatgen/ASE thermodynamic corrections | ΔG_H, ΔG_OH, ΔG_CO, ΔG_NNH 等 eV | distance to volcano optimum；hard stability/site-validity gates | C | P1/P2 |
| 催化/表面 | reaction barrier proxy / NEB barrier | initial/final/TS guess or path | OC20NEB/UMA+NEB/ASE；DFT labels if frozen | eV | minimize barrier + selectivity hard constraints | C/D | P2 |
| 反应/合成 | reaction yield | reaction SMILES + optional conditions | `rxn_yields`/Yield-BERT；ORD/HTE frozen labels；rxnfp | % yield continuous | maximize predicted yield；calibration/window by reaction family | B/C | P1 |
| 反应/合成 | retrosynthesis feasibility | target SMILES | AiZynthFinder；ASKCOS/local template planner；stock file snapshot | solved flag, route length, route score, stock cost | hard solved + minimize route depth/cost + SA/QED constraints | B | P1 |
| 反应/合成 | atom mapping / reaction-center validity | reaction SMILES | RXNMapper confidence；RDKit reaction validation | confidence 0-1, mapped reaction | maximize confidence; hard valid reaction SMILES and mass balance | A/B | P1 |
| 反应/合成 | condition suitability / selectivity | reaction + condition candidate | ORD labels, rxn condition prediction models, literature models | probability/yield/selectivity | only within named reaction family; otherwise defer | C/D | P2/P3 |
| 量子/分子 | HOMO-LUMO gap、orbital energies | 3D conformer/SMILES | xTB/GFN2-xTB；cclib parsers；QM9 labels/Uni-Mol+ | eV | target gap window or maximize/minimize | A/B | P0 |
| 量子/分子 | dipole moment、polarizability、partial charges | 3D conformer/SMILES | xTB/xtb-python；cclib；OpenFF/RDKit charge models | Debye, a.u., per-atom charges | target window or structural constraint; aggregate charge metrics | A/B | P0/P1 |
| 量子/分子 | conformer energy / strain / MMFF/OpenMM/xTB relative energy | SMILES + conformer | RDKit ETKDG/MMFF/UFF；OpenMM force-field minimization/MD；xTB/CREST | kcal/mol/eV | minimize strain or require low-energy conformer under property objective | A/B | P0 auxiliary |
| 量子/分子 | excitation energy / oscillator strength | 3D molecule | TDDFT output parsed by cclib; QM9/UV datasets; ML surrogates | eV, nm, oscillator strength | target wavelength/window + hard chemistry validity | C | P2 |

## 2. 证据来源摘要

本报告优先使用论文、官方文档、官方 GitHub/Hugging Face、数据集主页和模型文档。关键来源如下：

- 小分子/ADMET：ADMET-AI 官方仓库说明其使用 Chemprop 模型并训练于 TDC ADMET 数据集，支持 CLI、Python API 和 web server 预测 [ADMET-AI](https://github.com/swansonk14/admet_ai)；Chemprop 官方文档说明其是 PyTorch MPNN molecular property prediction 框架 [Chemprop docs](https://chemprop.readthedocs.io/en/latest/)；TDC ADMET group 列出 Caco-2、Bioavailability、Lipophilicity、Solubility、BBB、PPBR、VDss、CYP、hERG、AMES、DILI、LD50 等任务 [TDC ADMET](https://tdcommons.ai/benchmark/admet_group/overview/)；MoleculeNet/DeepChem 提供 ESOL、FreeSolv、Lipophilicity、BBBP、Tox21、ClinTox、SIDER、QM9 等标准数据加载入口 [DeepChem MoleculeNet](https://deepchem.readthedocs.io/en/latest/api_reference/moleculenet.html)。
- 小分子确定性工具：RDKit QED 文档说明 QED 是由 MW、logP、TPSA、HBD/HBA、rotatable bonds、aromatic rings、alerts 等性质映射出的 drug-likeness 度量 [RDKit QED](https://www.rdkit.org/docs/source/rdkit.Chem.QED.html)；RDKit SA_Score 是 Ertl/Schuffenhauer synthetic accessibility score 的 RDKit 实现 [RDKit SA_Score](https://github.com/rdkit/rdkit/tree/master/Contrib/SA_Score)。
- 小分子专用模型：OPERA 是开源/开放数据 QSAR 套件，提供 physicochemical、environmental fate、toxicity、Caco-2、FuB、Clint、pKa、logD、logP、水溶解度等模型 [OPERA](https://github.com/NIEHS/OPERA)；SolTranNet 是 fast aqueous solubility predictor，提供 CLI 和 Python API [SolTranNet](https://github.com/gnina/SolTranNet)；ChemBERTa 提供 SMILES transformer 预训练与 property prediction/fine-tuning notebook [ChemBERTa](https://github.com/seyonechithrananda/bert-loves-chemistry)；Uni-Mol 提供 3D molecular representation、property prediction、binding pose、quantum chemical property prediction [Uni-Mol](https://github.com/deepmodeling/Uni-Mol)；DeepPurpose/DeepDTA 支持 DTI/binding affinity proxy [DeepPurpose](https://github.com/kexinhuang12345/DeepPurpose)、[DeepDTA](https://github.com/hkmztrk/DeepDTA)。
- 数据库：ChEMBL web services 支持 activity 端点与 `pchembl_value`/`standard_value`/target 过滤 [ChEMBL web services](https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services)；PubChem PUG-REST 支持 compound property 访问 [PubChem PUG-REST](https://pubchemdocs.ncbi.nlm.nih.gov/pug-rest)。
- 材料：Materials Project API summary rester 支持 `band_gap`、`density`、`energy_above_hull`、`formation_energy`、`k_vrh`、`g_vrh`、`n` 等查询字段 [mp-api summary source](https://github.com/materialsproject/api/blob/main/mp_api/client/routes/materials/summary.py)；pymatgen PhaseDiagram 支持 energy-above-hull 计算 [pymatgen analysis docs](https://pymatgen.org/pymatgen.analysis.html)；Matbench 是 13 个材料性质预测任务的标准 benchmark，元数据包括 dielectric、experimental gap、MP formation energy、MP PBE gap、bulk/shear modulus 等 [Matbench](https://github.com/materialsproject/matbench)。
- 材料模型：MatGL 是 Materials Graph Library，包含 M3GNet/MEGNet/QET/CHGNet 等架构与预训练权重 [MatGL](https://matgl.ai/)；CHGNet 提供 Materials Project trajectory 预训练 universal neural network potential，可预测 energy/force/stress/magmom 并进行 relaxation [CHGNet](https://github.com/CederGroupHub/chgnet)；MACE 提供 ML interatomic potentials 和 MACE-MP foundation models [MACE](https://github.com/ACEsuit/mace)；MatterSim 提供 MatterSim-v1 pretrained atomistic models 和 ASE calculator [MatterSim](https://github.com/microsoft/mattersim)；MODNet/CrabNet/Roost/Matformer 覆盖 descriptor、composition-only 和 crystal graph property prediction [MODNet](https://github.com/ppdebreuck/modnet)、[CrabNet](https://github.com/anthony-wang/CrabNet)、[Roost](https://github.com/CompRhys/roost)、[Matformer](https://github.com/YKQ98/Matformer)。
- 催化：OC20 官方页面说明其包含 133M+ DFT calculations、约 460K adsorbate-catalyst relaxations、S2EF/IS2RE/IS2RS 任务 [OC20](https://fair-chem.github.io/oc20/)；FAIR-Chem v2/UMA 文档说明 UMA 训练于 500M+ DFT examples，覆盖 materials、molecules、catalysis，ASE calculator 可输出 energy/forces [FAIR-Chem](https://fair-chem.github.io/)、[UMA docs](https://github.com/facebookresearch/fairchem/blob/main/docs/core/uma.md)；EquiformerV2 官方实现给出 OC20 checkpoints 和 energy/force MAE [EquiformerV2](https://github.com/atomicarchitects/equiformer_v2)。
- 反应/合成：RXNMapper 提供 attention-guided atom mapping 和 confidence score [RXNMapper](https://github.com/rxn4chemistry/rxnmapper)；AiZynthFinder 是基于 MCTS 和模板策略网络的 retrosynthetic planning 工具，递归分解到 purchasable precursors [AiZynthFinder](https://github.com/MolecularAI/aizynthfinder)；`rxn_yields`/Yield-BERT 用 reaction SMILES 预测 yield [rxn_yields](https://github.com/rxn4chemistry/rxn_yields)；Open Reaction Database 提供开放 reaction schema/data [ORD schema](https://github.com/open-reaction-database/ord-schema)。
- 量子/分子模拟：xTB 文档显示默认输出 HOMO/LUMO、HOMO-LUMO gap、dipole、partial charges、polarizability 等 [xTB properties](https://xtb-docs.readthedocs.io/en/latest/properties.html)；xtb-python 提供 Calculator API 并可读取 energy、gradient、charges [xtb-python](https://xtb-python.readthedocs.io/en/latest/)；cclib 可解析 quantum chemistry 输出中的 atomcharges、moenergies、moments、polarizabilities、etenergies、etoscs 等 [cclib data](https://cclib.github.io/data.html)；OpenMM 是高性能 molecular simulation toolkit，支持 Python API、energy minimization、MD、GPU/CPU platforms 和 custom forces，可作为力场能量、构象稳定性、短程 MD sanity、相对/绝对自由能 workflow 的底层 verifier [OpenMM](https://openmm.org/)、[OpenMM User Guide](https://docs.openmm.org/development/userguide/)。

## 3. 候选性质逐项分析

### 3.1 小分子/药物化学

#### 3.1.1 RDKit 药物样性质：QED、logP、TPSA、MW、HBD/HBA、rotatable bonds

研究价值：这些是药物化学中最常见的早期过滤指标。QED 明确把 logP、TPSA、HBD/HBA、rotatable bonds、aromatic rings、unwanted functionalities 等映射为 drug-likeness 分数，适合做开放生成分子的基础质量门槛。

verifier：

- RDKit：SMILES -> Mol -> descriptors/QED；完全本地、确定性、低成本。
- PubChem PUG-REST：可作外部标签/交叉检查，但首版不应依赖实时网络。

输入/输出：

- 输入：canonical SMILES 或 InChI。
- 输出：`qed` in [0,1]；`MolLogP`；TPSA；MW；HBD/HBA 计数；rotatable bonds。

适合性：

- 非常适合 open generation。agent 必须生成有效结构并满足多目标约束，skills-on agent 可用 RDKit 迭代修正，skills-off LLM 容易只给常见药物或无效 SMILES。
- 数据泄漏低，因为 verifier 是确定性公式，不是记忆标签。

失败模式：

- 无效 SMILES、盐/混合物、金属配合物、tautomer/protonation 未定义。
- 公式性指标容易被 adversarial molecule 利用，所以应与 SA、ADMET 或 novelty/structure sanity gate 组合。

推荐评分：

- `validity_gate * uniqueness_gate * exp(-distance_to_window / scale)`。
- 示例：`0.2 <= logP <= 3.5`、`TPSA <= 90`、`QED >= 0.6`；目标可以是 maximize QED under SA/logP/TPSA constraints。

评级：A，P0。

#### 3.1.2 SA score / synthesizability proxy

研究价值：开放生成分子若只优化 QED/logP，容易生成不可合成或奇异结构。SA score 是生成模型 benchmark 中常用的基础约束。

verifier：

- RDKit Contrib `SA_Score`，实现 Ertl/Schuffenhauer synthetic accessibility score。
- AiZynthFinder 可作为更强但更重的 retrosynthesis feasibility verifier。

输入/输出：

- 输入：SMILES。
- 输出：SA score，通常低分更易合成。

适合性：

- 很适合做 hard constraint 或 secondary objective。
- 适合区分 skills-on agent，因为 agent 可用 fragment/analog search 优化结构；skills-off LLM 常忽略合成可及性。

失败模式：

- SA score 是 heuristic，不等同真实 route availability；macrocycle、organometallic、天然产物类可能被误判。

推荐评分：

- `SA <= 4` hard pass；或 minimize SA，配合 QED/ADMET objective。

评级：A，P0。

#### 3.1.3 溶解度 logS / log aqueous solubility

研究价值：水溶解度直接影响 oral exposure、formulation、screening success，是 ADMET/QSPR 中最常见的连续性质之一。

verifier：

- SolTranNet：官方实现提供 CLI 与 Python API，输入 SMILES 输出 aqueous solubility prediction。
- ADMET-AI：ADMET 预测平台，包含 solubility/lipophilicity/hydration endpoints。
- OPERA：提供 WS(log mol/L) 等开放 QSAR 模型。
- 冻结标签：MoleculeNet ESOL/AqSolDB/TDC Solubility_AqSolDB。

输入/输出：

- 输入：SMILES。
- 输出：logS 或模型定义的 aqueous solubility 连续值。

适合性：

- 适合 P0。性质连续、低成本、可本地批量跑。
- 对 skills-on 有区分度：可以使用模型筛选 analogs 或做局部结构修改。

失败模式：

- 盐、两性离子、混合物、tautomer/protonation 状态会影响预测；不同模型单位/定义不同。
- 模型训练集公开，过于常见分子可能被语言模型或数据库记忆。

推荐评分：

- 目标窗口：例如 `-4 <= logS <= -1`；或 maximize solubility while `QED >= 0.5` and `SA <= 4.5`。
- 首版固定一个模型和版本，报告单位和适用域；对 invalid/unsupported structure 给 0 分。

评级：A/B，P0。

#### 3.1.4 logD / pKa

研究价值：pKa 与 logD 决定离子化、膜通透、溶解度和分布，是 medicinal chemistry optimization 中的重要性质。

verifier：

- OPERA：官方模型列表包含 pKa 与 LogD。
- ADMET-AI：physicochemical properties 中包含 LogP/LogD/LogS/MW/pKa。
- RDKit 可算 logP，但不能可靠给 pKa/logD。

输入/输出：

- 输入：SMILES；需要定义 pH，例如 logD7.4。
- 输出：酸/碱 pKa、major pKa、logD。

适合性：

- 适合 P1。真实价值高，但多中心 pKa、tautomer、protonation definition 容易引起评分歧义。

失败模式：

- 模型对 zwitterions、macrocycles、boronic acids、organometallics 适用域差。
- 若 verifier 使用 web service，版本冻结较难；应优先本地 OPERA/ADMET-AI checkpoint。

推荐评分：

- CNS 题：`7.4 logD in [1,3]` + `basic pKa in [6,9]`。
- 非 CNS 题：按目标组织/吸收目标设置窗口。

评级：B，P1。

#### 3.1.5 毒性与安全：hERG、AMES、DILI、LD50、ClinTox/Tox21

研究价值：hERG、mutagenicity、DILI、acute toxicity 是药物发现中早期 safety triage 的关键 endpoint。它们经常被 QSAR/ML 模型预测。

verifier：

- ADMET-AI：toxicity、hERG、DILI、LD50 等。
- OPERA：CATMoS acute toxicity、CERAPP/CoMPARA nuclear receptor activity、环境/毒理 QSAR。
- TDC/MoleculeNet：hERG、AMES、DILI、Tox21、ClinTox 等 benchmark labels。

输入/输出：

- 输入：SMILES。
- 输出：分类概率、active/inactive label、LD50 log mg/kg。

适合性：

- 适合 P0/P1，尤其适合 multi-objective benchmark：在保持 potency/QED/solubility 的同时降低 hERG/tox risk。
- skills-on agent 能运行 ADMET 预测并做结构修改；skills-off LLM 容易只列常识性“低毒”结构。

失败模式：

- 分类模型校准差、类别不平衡、endpoint 数据噪声大。
- 对 novel chemistry 的 extrapolation 风险高；可能奖励模型漏洞而非真实安全。

推荐评分：

- hard gate：`p(hERG active) < 0.2`、`p(AMES positive) < 0.2`。
- soft objective：`score = 1 - max(tox_probs)` 或 toxicity desirability geometric mean。
- 必须加 applicability-domain 或 uncertainty filter；对 out-of-domain 分子降权。

评级：B，P0/P1。

#### 3.1.6 ADME：CYP、BBB、Caco-2、HIA、P-gp、PPB、clearance、VDss

研究价值：ADME 决定 exposure、drug-drug interaction、CNS penetration、oral absorption，是 lead optimization 的真实多目标核心。

verifier：

- ADMET-AI：BBB penetrance、bioavailability、clearance/distribution、CYP interactions、physicochemical、solubility/lipophilicity/hydration。
- TDC ADMET group：Caco2_Wang、Bioavailability_Ma、Lipophilicity_AstraZeneca、Solubility_AqSolDB、BBB_Martins、PPBR_AZ、VDss_Lombardo、CYP tasks 等。
- OPERA：Caco-2、FuB、Clint 等。

输入/输出：

- 输入：SMILES。
- 输出：probability 或 continuous ADME 值，例如 Caco-2 logPapp、PPB fraction、clearance、VDss。

适合性：

- 适合 P1，作为“多目标药物设计”比单一 logP/QED 更真实。
- 首版不要覆盖太多 endpoint；选择 2-3 个组合即可，例如 Caco-2 maximize + hERG minimize + solubility window。

失败模式：

- 单 endpoint 模型噪声大，物种/实验协议差异大。
- clearance/PPB/VDss 往往数据少、外推风险高。

推荐评分：

- `hard constraints`: valid, SA, MW, hERG/AMES safe。
- `soft objective`: weighted desirability over Caco-2/BBB/CYP/PPB。

评级：B，P1。

#### 3.1.7 靶点活性 / binding affinity proxy

研究价值：真实药物发现的优化对象通常不是“漂亮分子”，而是对特定 target 的 potency/selectivity，同时满足 ADMET。

verifier：

- 冻结 ChEMBL：按 target、assay type、standard type、pChEMBL 过滤，取 pChEMBL 或 transformed IC50/Ki/Kd。
- DeepPurpose：DTI、drug property、virtual screening；支持 pretrained BindingDB models。
- DeepDTA：SMILES + protein sequence -> binding affinity prediction。
- TDC DTI/KIBA/DAVIS/BindingDB tasks。

输入/输出：

- 输入：candidate SMILES + target id/sequence；或者候选需匹配冻结数据库中的 molecule-target pair。
- 输出：pChEMBL/pKd/pKi/pIC50 或 predicted affinity。

适合性：

- 科学价值很高，但首版要谨慎。若用数据库标签，开放生成会退化成“找到已知活性分子”；若用 ML verifier，可能奖励模型漏洞。
- 更适合 P1：限制 target family、要求 novelty relative to label set、加入 ADMET gates。

失败模式：

- assay heterogeneity、unit conversion、censored values、PAINS/promiscuity。
- 数据泄漏很高：ChEMBL/BindingDB 与公开模型训练集重叠，语言模型可能记忆知名 inhibitors。

推荐评分：

- 两种模式：
  - frozen-label mode：候选必须 canonicalized 后命中冻结 ChEMBL snapshot；score = normalized pChEMBL；适合 database-search skill。
  - model mode：DeepPurpose/DeepDTA frozen checkpoint；score = predicted pAffinity with uncertainty/novelty penalty。

评级：B/C，P1。

### 3.2 材料化学

#### 3.2.1 band gap / metal-nonmetal

研究价值：band gap 是光伏、半导体、透明导体、光催化、绝缘材料设计的核心性质。它是材料 ML 最常见的性质预测任务之一。

verifier：

- Materials Project frozen labels/API：`band_gap`。
- Matbench：`matbench_mp_gap`、`matbench_expt_gap`、`matbench_mp_is_metal`。
- ML 模型：MEGNet/MatGL、Matformer、MODNet/CrabNet/Roost、CHGNet/relaxed structure + property model。

输入/输出：

- 输入：CIF、pymatgen Structure、composition；composition-only 模型可接受 formula。
- 输出：eV 或 is_metal probability/class。

适合性：

- 非常适合 P0。开放生成材料需要结构有效、成分合理、可通过数据库或 ML 模型验证。
- skills-on agent 可以查询 MP/Matbench、使用 pymatgen 生成/校验结构；skills-off LLM 很难生成有效 CIF 或稳定材料。

失败模式：

- DFT/PBE band gap systemically underestimated；experimental gap 与 computed gap 定义不同。
- 对 novel generated structures，用 database labels 不可用；用 ML verifier 要做结构 sanity/relaxation。

推荐评分：

- 目标窗口：photovoltaic-like `1.1-1.8 eV`，wide-gap `>3 eV`，transparent conductor `>3 eV + stability`。
- hard gate：valid structure, charge-balance optional, density range, E_hull threshold。

评级：A/B，P0。

#### 3.2.2 formation energy、energy above hull、stability proxy

研究价值：没有 thermodynamic/metastability 约束的材料生成会产生大量不可实现结构。Formation energy 与 E above hull 是 computational materials discovery 的基础目标。

verifier：

- Materials Project summary：`formation_energy_per_atom`、`energy_above_hull`、`is_stable`。
- pymatgen PhaseDiagram：用冻结 entry set 计算 E above hull。
- CHGNet/M3GNet/MatGL/MACE/MatterSim：对 generated structure 做 relaxation 和 energy prediction，再与参考元素/相图估算 stability proxy。

输入/输出：

- 输入：CIF/Structure/composition。
- 输出：eV/atom；stable boolean。

适合性：

- 适合 P0，但需要明确模式：
  - database-label mode：候选命中 MP/JARVIS/COD snapshot。
  - generated-structure mode：ML potential relax + energy + frozen phase diagram。

失败模式：

- 相图需要一致的 energy correction/reference；ML potential energy 不一定可直接与 DFT formation energy 对齐。
- metastable useful materials 会被 strict E_hull 过度惩罚。

推荐评分：

- `E_hull <= 0.05 eV/atom` full stability credit；`0.05-0.1` partial；`>0.2` near-zero。
- 组合：band gap window + minimize E_hull。

评级：B，P0。

#### 3.2.3 elastic modulus：bulk/shear modulus、Poisson ratio

研究价值：mechanical stability、hardness、structural material screening、battery solid electrolyte mechanical compatibility 都依赖 elastic properties。

verifier：

- Materials Project elastic fields：`k_vrh`、`g_vrh`、`poisson_ratio` 等。
- Matbench：`matbench_log_kvrh`、`matbench_log_gvrh`。
- CHGNet/atomate2 elastic workflow：通过 stress-strain 计算 elastic tensor，成本比单点高。

输入/输出：

- 输入：Structure/CIF。
- 输出：GPa 或 log10(GPa)。

适合性：

- P1。真实价值高，但结构 relaxation 与 stress accuracy 需要更严格控制。

失败模式：

- soft/unstable structures、低维材料、分子晶体、含 disorder 结构容易失败。
- ML potentials 的 stress/elastic tensor 可靠性低于 energy/force。

推荐评分：

- hard gate：E_hull/stability + density sanity。
- objective：maximize K/G，或 target window for flexible/soft materials。

评级：B/C，P1。

#### 3.2.4 dielectric constant / refractive index

研究价值：介电常数和折射率用于电容器、绝缘体、光学材料、显示/光子学材料筛选。

verifier：

- Materials Project dielectric fields：`e_total`、`e_electronic`、`e_ionic`、`n`。
- Matbench `matbench_dielectric`：target `n`。
- MODNet pretrained refractive index model。

输入/输出：

- 输入：Structure/CIF。
- 输出：dimensionless dielectric constant or refractive index。

适合性：

- P1。比 band gap/stability 稍窄，但适合生成 “high-k stable insulator” 或 “high refractive index, wide-gap” benchmark。

失败模式：

- DFT workflow 不是所有材料都有；对 metals/low-gap systems 不适用。
- 结构质量和 band gap gate 很重要。

推荐评分：

- `band_gap > 2 eV` hard gate；maximize `e_total` 或 target `n`。

评级：B，P1。

#### 3.2.5 density / volume / composition sanity

研究价值：密度本身可用于 energetic materials、lightweight alloys、porous materials；更重要的是作为 open-generation 结构 sanity gate。

verifier：

- pymatgen Structure density/volume。
- Materials Project summary `density`/`volume`。

输入/输出：

- 输入：Structure/CIF。
- 输出：g/cm3、A3/atom、composition。

适合性：

- 单独作为目标太容易，但作为 hard constraint 非常有用。

失败模式：

- CIF partial occupancy/disorder；unphysical cell。

推荐评分：

- 作为所有材料任务的 hard gate：density in plausible range, no atom overlap, charge-balanced optional。

评级：A，P0 auxiliary。

#### 3.2.6 thermal conductivity / ionic conductivity

研究价值：热导用于 thermoelectrics、heat management；离子电导用于 solid electrolytes/batteries。科学价值很高。

verifier：

- 专用数据库/模型：JARVIS、Matminer datasets、文献 ML 模型；MD/phonon workflow 可计算但成本高。
- 通用 CHGNet/MACE/MatterSim 可跑 MD/phonons，但不等于可靠 conductivity verifier。

输入/输出：

- 输入：Structure/composition。
- 输出：W/mK 或 S/cm。

适合性：

- 暂不建议 P0。适合后续窄域任务，例如 Li solid electrolytes with frozen dataset labels。

失败模式：

- 计算成本高；温度、defects、grain boundary、disorder、migration path 定义复杂。

推荐评分：

- 仅限 frozen benchmark labels or narrow pretrained model；maximize/minimize with uncertainty penalty。

评级：C/D，P2/P3。

### 3.3 催化/表面化学

#### 3.3.1 adsorption energy / binding energy

研究价值：adsorption energy 是 heterogeneous catalysis descriptor 的核心。HER/ORR/CO2RR/NRR 等都常用中间体吸附自由能构建 volcano relationships。

verifier：

- Open Catalyst OC20/OC22 frozen labels：S2EF、IS2RE、IS2RS 等任务。
- FAIR-Chem UMA：使用 `task_name="oc20"` 或相关 task 预测 energy/forces，ASE calculator 支持 batch inference。
- GemNet-OC、EquiformerV2：OC20 上的公开模型/checkpoints。
- ASE+pymatgen：构建 slab/adsorbate、relax、计算 energy difference。

输入/输出：

- 输入：slab structure + adsorbate/intermediate placement，或 candidate catalyst composition + generated adsorption site template。
- 输出：adsorption energy/binding energy in eV；relaxed energy/force。

适合性：

- P1。科学价值高，但 input schema 比小分子/材料复杂。首版应限制在固定 adsorbate set 和 fixed slab templates。

失败模式：

- 生成 slab/site 的合法性、coverage、spin/charge、solvent/potential effects 未定义。
- OC20 不含 oxides/explicit solvents，模型文档也提示适用域限制。

推荐评分：

- `E_ads = E(slab+adsorbate) - E(slab) - E(adsorbate)`，用同一 frozen model 计算。
- target descriptor window，例如 HER `|ΔG_H|` 接近 0；CO2RR 可设 `ΔG_CO` window + weak H binding。

评级：B/C，P1。

#### 3.3.2 volcano descriptors：HER/ORR/CO2RR/NRR 中间体自由能

研究价值：volcano descriptor 是催化筛选的经典可解释目标，例如 HER 的 ΔG_H、ORR 的 ΔG_OOH/ΔG_O/ΔG_OH、CO2RR 的 CO/*CHO/*COOH binding。

verifier：

- OC/UMA/GemNet/EquiformerV2 计算 adsorption energies。
- 热力学校正表和 reference molecule energies 冻结。

输入/输出：

- 输入：catalyst surface + one or more intermediate adsorbates。
- 输出：多个 ΔG descriptor in eV。

适合性：

- P1/P2。比单一 adsorption energy 更真实，也更复杂。适合 skills-on agent，因为需要构建多个 intermediates、计算相对能量和判定 tradeoff。

失败模式：

- 自由能修正、pH/potential、site matching、adsorbate conformer 都会引入任意性。

推荐评分：

- `score = exp(-|ΔG_H - 0|/scale)` for HER。
- CO2RR/ORR 使用 hard selectivity constraints，例如 target intermediate binding stronger than competing H by margin。

评级：C，P1/P2。

#### 3.3.3 reaction barrier proxy / transition-state barrier

研究价值：真实催化活性最终取决于 barriers，不只是 thermodynamic descriptors。

verifier：

- OC20NEB/NEB-style datasets、UMA/ASE NEB relaxation、DFT labels。

输入/输出：

- 输入：initial/final/TS path 或 reaction template on surface。
- 输出：activation barrier eV。

适合性：

- 暂不建议首版。输入规范和失败处理都复杂，计算成本也高。

推荐评分：

- narrow family only：minimize barrier under adsorption-energy and stability constraints。

评级：C/D，P2。

### 3.4 反应/合成

#### 3.4.1 reaction yield

研究价值：reaction yield 是 reaction optimization 的直接目标，HTE 和自动化实验大量使用 ML 预测 yield。

verifier：

- `rxn_yields` / Yield-BERT：reaction SMILES -> yield regression。
- ORD/HTE frozen labels：适合 reaction-family-specific tasks。
- rxnfp reaction fingerprints 可作为特征/模型基础。

输入/输出：

- 输入：reaction SMILES；可选 reactants/reagents/solvent/base/catalyst/temperature schema。
- 输出：predicted yield %。

适合性：

- P1。适合开放生成反应条件，但必须限制 reaction family；通用 yield verifier 不稳。

失败模式：

- USPTO yield 噪声大，`rxn_yields` README 也指出 patent yield distribution/noise 问题。
- 条件文本规范化难；invalid reaction SMILES、多产物、stoichiometry 缺失。

推荐评分：

- family-specific：Buchwald-Hartwig 或 Suzuki-Miyaura HTE；agent 生成 ligand/base/solvent/substrate combination。
- score = predicted/label yield, with hard validity and availability constraints。

评级：B/C，P1。

#### 3.4.2 retrosynthesis feasibility

研究价值：开放生成的 molecule/material precursor 如果无法合成，benchmark 会奖励不实用候选。Retrosynthesis feasibility 是 real-world medicinal chemistry 的关键 secondary objective。

verifier：

- AiZynthFinder：MCTS recursively breaks target to purchasable precursors；需要 stock file、expansion policy、optional filter policy。
- 可冻结 stock snapshot 和 template model checkpoint。

输入/输出：

- 输入：target SMILES。
- 输出：solved/unsolved、route length、route score、precursor availability、estimated cost。

适合性：

- P1。尤其适合作为 molecule design hard gate。

失败模式：

- route score 依赖 stock 文件和模板覆盖；novel chemistry 可能被误判不可行。
- 计算成本中等；batch 多时需缓存。

推荐评分：

- hard solved；soft objective minimize route length/precursor cost/number of steps。

评级：B，P1。

#### 3.4.3 atom mapping / reaction-center validity

研究价值：反应生成任务常产生不守恒、不合理或无法解释的 reaction SMILES。Atom mapping 和 reaction center validity 是自动 verifier 的低成本 sanity check。

verifier：

- RXNMapper：valid reaction SMILES -> mapped reaction + confidence。
- RDKit reaction parsing/mass balance/changed-bond analysis。

输入/输出：

- 输入：reaction SMILES。
- 输出：mapped reaction, confidence score, validity flags。

适合性：

- P1，适合作为 reaction benchmark 的 hard gate 或 low-cost target。

失败模式：

- 高 confidence 不等于反应真实可行；reagents/solvents may be ignored。

推荐评分：

- hard valid + mass-balanced + confidence > threshold；soft maximize confidence 或 penalize impossible atom creation/loss。

评级：A/B，P1。

#### 3.4.4 condition suitability / selectivity

研究价值：真实合成优化常优化 solvent/base/catalyst/temperature 和 regio-/stereo-/chemo-selectivity。

verifier：

- ORD frozen labels、family-specific HTE datasets、文献 reaction condition prediction models。

适合性：

- P2/P3。科学价值高，但通用 verifier 不成熟；首版只应做窄域 family-specific。

推荐评分：

- 仅对固定 reaction family，candidate condition schema 固定；score = predicted yield/selectivity。

评级：C/D，P2/P3。

### 3.5 量子化学/分子性质

#### 3.5.1 HOMO-LUMO gap、orbital energies

研究价值：HOMO-LUMO gap 是光电、有机电子、反应性、stability proxy 的常见性质。QM9/PCQM4Mv2/Uni-Mol+ 等 benchmark 也常预测量子性质。

verifier：

- xTB/GFN2-xTB：默认 property printout 包含 HOMO/LUMO 和 HOMO-LUMO gap。
- xtb-python：本地 API，支持 energy/gradient/charges；orbital/gap 可通过 output parser 或 xTB CLI。
- cclib：解析 quantum chemistry 输出的 `moenergies`、`homos` 等。
- QM9/MoleculeNet frozen labels，Uni-Mol+ quantum property model。

输入/输出：

- 输入：SMILES -> RDKit conformer -> xTB singlepoint/optimization；或 direct 3D geometry。
- 输出：eV gap、HOMO/LUMO energies。

适合性：

- P0。比 DFT 便宜，能强区分会调用工具的 agent。

失败模式：

- Conformer dependence；charged/radical/open-shell systems；xTB parameter domain。
- 对金属配合物、重元素、异常 valence 需限制。

推荐评分：

- target window，例如 OLED donor/acceptor gap 或 low-gap organic semiconductor；hard constraints on neutral organic subset。

评级：A/B，P0。

#### 3.5.2 dipole moment、polarizability、partial charges

研究价值：dipole 和 polarizability 影响 solvation、crystal packing、dielectric response、binding；partial charges 是 docking/force-field/interaction modeling 的基础。

verifier：

- xTB properties：dipole、charges、polarizability。
- xtb-python：energy/gradient/charges API。
- cclib：`atomcharges`、`moments`、`polarizabilities`。
- OpenFF/RDKit charge models 可作为低成本替代，但物理层级较低。

输入/输出：

- 输入：3D molecule or SMILES+conformer。
- 输出：Debye、a.u. polarizability、per-atom charge vector。

适合性：

- P0/P1。适合做 “generate polar molecule within drug-like constraints” 或 “low dipole/high polarizability chromophore”。

失败模式：

- 构象依赖强；partial charge scheme 不同不可混用。
- 大分子/盐/charged species 需要清晰定义。

推荐评分：

- target dipole window；maximize polarizability per heavy atom under MW/SA constraints；charge distribution constraints for binding motifs。

评级：A/B，P0/P1。

#### 3.5.3 conformer energy / strain / relative energy

研究价值：低能构象和 strain 与 binding、crystal packing、synthetic plausibility 有关，也是开放生成结构的 sanity gate。

verifier：

- RDKit ETKDG + MMFF/UFF energy。
- OpenMM：用 OpenFF/GAFF/AMBER 等 force field 参数化后进行 energy minimization、短程 MD 或 potential energy sanity check；更适合作为辅助 gate，不建议首版用长程 MD 指标做核心分数。
- xTB optimize/singlepoint；CREST for conformer ensemble if higher cost acceptable。

输入/输出：

- 输入：SMILES or 3D conformer。
- 输出：relative conformer energy、strain proxy、optimization convergence。

适合性：

- P0 auxiliary。单独作为目标容易被 trivial molecules 满足；与 ADMET/QED/quantum objectives 组合效果好。

失败模式：

- force field parameter gaps；macrocycles/metal complexes；多构象采样随机性。

推荐评分：

- hard gate：successful conformer generation and xTB convergence。
- soft objective：minimize strain energy or require low-energy conformer count/diversity。

评级：A/B，P0 auxiliary。

#### 3.5.4 excitation energy / oscillator strength

研究价值：光吸收/发光材料、photochemistry、dye design 需要 excitation energies 和 oscillator strengths。

verifier：

- TDDFT output parsed by cclib (`etenergies`, `etoscs`)。
- UV datasets / specialized ML models。

适合性：

- P2。科学价值高但计算成本和 method-dependence 高。可以后续做 small organic chromophore domain。

推荐评分：

- target absorption wavelength window + oscillator strength threshold + xTB/DFT convergence gates。

评级：C，P2。

## 4. Verifier 设计原则

### 4.1 输入规范

推荐首版支持以下 candidate schemas：

| 对象 | 最小输入 | canonicalization | validity gate |
|---|---|---|---|
| 小分子 | SMILES | RDKit canonical SMILES/InChIKey | sanitization、single component、allowed elements、MW range |
| 材料 | CIF or pymatgen Structure JSON | pymatgen Structure normalization | no severe atom overlap、density range、charge/oxidation optional、space group optional |
| 催化剂 | ASE atoms for slab+adsorbate, fixed adsorbate name | sorted atoms + metadata schema | adsorbate present、surface cell valid、site distance sanity |
| 反应 | reaction SMILES + optional condition JSON | RDKit reaction parsing + RXNMapper | reactant/product validity、atom mapping/confidence、mass balance optional |
| 量子分子 | SMILES + generated conformer or XYZ | canonical SMILES + conformer seed/version | conformer generation, optimization convergence |

### 4.2 评分方式模板

通用 scoring function：

```text
score = validity_gate
      * domain_gate
      * novelty_or_leakage_gate
      * property_score
```

常用 property score：

- 窗口约束：`property_score = 1` if `L <= y <= U`; outside window use `exp(-distance_to_window / sigma)`。
- 目标值距离：`exp(-abs(y - target) / sigma)`。
- maximize/minimize：用 clipped linear 或 logistic desirability，避免极端 outlier 过度奖励。
- hard constraint + soft objective：例如 `E_hull <= 0.1`、`SA <= 4.5`、`valid hERG <= threshold` 后，再优化 band gap/QED/yield。
- 多目标：geometric mean of desirability，避免一个性质极高掩盖另一个性质失败。

### 4.3 冻结与可复现

每个 verifier 应冻结：

- package versions：RDKit、pymatgen、xTB、xtb-python、cclib、ASE、OpenMM 等。
- model checkpoint：ADMET-AI、Chemprop、SolTranNet、OPERA、CHGNet、MACE、MatterSim、FAIR-Chem/UMA、RXNMapper、AiZynthFinder policies。
- database snapshot：ChEMBL、PubChem property dump、Materials Project summary docs、Matbench datasets、OC20/OC22 labels、ORD datasets。
- random seeds：conformer generation、ML inference if stochastic、relaxation settings。

### 4.4 数据泄漏与 benchmark gaming

风险等级：

- 低：RDKit formula/descriptors、pymatgen structural descriptors、xTB singlepoint on generated geometry。
- 中：public pretrained ML model outputs。agent 可能也调用同一模型优化，但这正好能区分 skills-on；若不希望 gaming，应使用 hidden ensemble verifier。
- 高：public database labels for famous compounds/materials。LLM 可能记住 aspirin/logP、known inhibitors、MP material ids。

缓解策略：

- 使用 generated candidate 而非候选选择；要求 canonical novelty against frozen label set。
- 对 database-label tasks 使用 hidden target intervals 或 private split。
- 对 ML verifier 加 applicability-domain/uncertainty gate；对 extreme exploited structures 降权。
- 把 “agent 可使用公开工具寻找候选” 和 “verifier 独立冻结评分” 明确区分；首版可接受公开工具优化，但应在 report 中标注 leaderboard regime。

## 5. 首版 benchmark 推荐优先进入的 10 类性质

### P0-1 RDKit QED/logP/TPSA/MW + SA score 多约束小分子设计

原因：最稳定、成本最低、可本地确定性验证。可作为所有小分子任务的基础 sanity layer。能快速暴露无效 SMILES、过大分子、不可合成结构。

建议任务形态：

- “生成一个 neutral organic molecule，使 QED >= 0.75、SA <= 3.5、logP in [1,3]、TPSA in [40,90]，最大化 QED。”

### P0-2 水溶解度 logS

原因：真实 ADMET 价值高，SolTranNet/ADMET-AI/OPERA 都能本地或冻结模型验证，输出连续，适合 open generation。

建议任务形态：

- “在 QED/SA/MW 约束下最大化 predicted logS” 或 “logS 落入目标窗口，同时避免 hERG 风险。”

### P0-3 hERG/AMES/DILI 等 safety endpoint

原因：药物发现中真实重要，TDC/MoleculeNet/ADMET-AI/OPERA 支持成熟。建议作为 hard safety constraint，而不是单独目标。

建议任务形态：

- “生成高 QED、高溶解度、低 hERG/AMES 风险的小分子。”

### P0-4 材料 band gap

原因：材料 ML 最成熟性质之一；MP/Matbench/MEGNet/Matformer 等都覆盖；结构或成分输入清晰。

建议任务形态：

- “生成一个稳定无机晶体候选，band gap 在 1.2-1.8 eV，E_hull <= 0.1 eV/atom。”

### P0-5 formation energy / energy above hull

原因：材料生成的基础 feasibility gate。没有稳定性评分，open-generation 材料题很容易变成无意义结构生成。

建议任务形态：

- “在给定元素集合中生成 E_hull 最低且 band gap 满足窗口的结构。”

### P0-6 HOMO-LUMO gap

原因：xTB 本地可跑，连续数值，成本低于 DFT，适合小分子开放生成并能区分 tool use。

建议任务形态：

- “生成 neutral organic molecule，使 xTB HOMO-LUMO gap 接近 3.0 eV，同时 SA <= 4.5。”

### P0-7 dipole/polarizability/partial-charge 指标

原因：xTB/cclib 可验证，能引导 agent 做结构层面推理，不只是查数据库。适合作为 secondary objective 或 physical descriptor task。

建议任务形态：

- “生成低分子量、高偶极矩但 logP 合理的分子” 或 “最大化 polarizability per heavy atom under SA/MW constraints。”

### P1-8 ADME multi-endpoint：Caco-2/BBB/CYP/PPB/clearance

原因：真实药化价值很高，但 endpoint 定义和模型适用域比 logS/hERG 更复杂。建议首版作为少量 multi-objective 题，而不是全面覆盖。

建议任务形态：

- “生成 CNS-like molecule：BBB positive probability high、CYP inhibition low、hERG low、logD7.4 in target range。”

### P1-9 靶点活性 / binding affinity proxy

原因：与真实 drug discovery 目标最接近，但泄漏和 assay heterogeneity 高。建议先做固定 target/frozen ChEMBL snapshot 或 frozen DTA checkpoint。

建议任务形态：

- “对某 target 生成候选，maximize predicted pAffinity，同时通过 ADMET/SA gates；不得命中训练/标签集 exact molecule。”

### P1-10 adsorption energy / HER descriptor

原因：催化方向的代表性质，OC20/UMA/EquiformerV2 等 verifier 生态较强。输入复杂但可以通过固定 slab templates 降低难度。

建议任务形态：

- “在给定金属 surface template 上选择 alloy/composition/site，使 ΔG_H 接近 0 eV，并满足 slab stability proxy。”

### P1-11 reaction yield / retrosynthesis feasibility / RXNMapper validity

原因：反应和合成方向需要进入首版但应窄域。RXNMapper 可做 low-cost validity，AiZynthFinder 可做 molecular generation feasibility，Yield-BERT 可做 family-specific yield。

建议任务形态：

- “给定 Buchwald-Hartwig/Suzuki family，生成 reactant/ligand/base/solvent condition，maximize predicted yield。”
- “生成目标分子时必须被 AiZynthFinder 在 frozen stock 下找到 route，route length 越短越好。”

## 6. 不建议首版作为核心目标的性质

| 性质 | 不建议原因 | 后续可行路线 |
|---|---|---|
| 通用 reaction condition suitability | 条件 schema 与数据标准化难，模型泛化弱 | 限制在单一 reaction family + HTE/ORD frozen labels |
| 通用 selectivity | regio/stereo/selectivity 标签少且定义复杂 | 针对固定 substrate family 和 selectivity label |
| 通用 reaction barrier | TS/path 输入复杂，NEB 成本高 | 使用 OC20NEB/固定 elementary step |
| thermal conductivity | phonon/BTE/MD 成本高，结构缺陷敏感 | 使用 narrow material class + frozen labels |
| ionic conductivity | defect/path/temperature/phase strongly coupled | 固定固态电解质数据集和结构模板 |
| excitation energy | TDDFT 成本高，solvent/conformer/method dependence | 小型 organic chromophore + frozen TDDFT/ML surrogate |

## 7. 工程实现建议

首版 benchmark runner 未来应按 verifier 层分离：

1. `canonicalize`: SMILES/CIF/reaction/ASE structure canonicalization。
2. `validity`: chemical validity、domain validity、sanity constraints。
3. `predict`: frozen deterministic tool/model/database query。
4. `score`: task-specific continuous scoring。
5. `audit`: record verifier version, model checkpoint hash, input canonical form, warnings, failure code。

建议首版 verifier 包：

- `small_molecule_rdkit`: QED/logP/TPSA/MW/SA。
- `small_molecule_admet`: ADMET-AI/SolTranNet/OPERA subset。
- `materials_mp_matbench`: MP/Matbench frozen label lookup + pymatgen validity。
- `materials_mlip`: CHGNet/MatGL or MatterSim relaxation/energy proxy。
- `quantum_xtb`: RDKit conformer + xTB singlepoint/optimization + cclib/regex parse。
- `reaction_validity`: RXNMapper + RDKit reaction checks。
- `synthesis_feasibility`: AiZynthFinder frozen policy/stock。
- `catalysis_oc`: fixed slab/intermediate templates + FAIR-Chem UMA or OC labels.

## 8. 需求覆盖核对

- 覆盖方向：小分子/ADMET、材料、催化/表面、反应/合成、量子性质均已覆盖。
- 每个候选性质分析维度：研究价值、常见预测方式、数值表达、开放生成验证、冻结/本地部署、成本/失败/适用域/泄漏、skills-on 区分度均在各节或总原则中说明。
- verifier 来源：RDKit、pymatgen、cclib、xTB、OpenMM、ASE、Chemprop、ADMET-AI、OPERA、SolTranNet、ChemBERTa、Uni-Mol、DeepPurpose、DeepDTA、MatGL/M3GNet/MEGNet、CHGNet、MACE、MatterSim、MODNet、CrabNet、Roost、Matformer、Open Catalyst/FAIR-Chem/UMA、GemNet-OC/EquiformerV2、ChEMBL、PubChem、Materials Project、Matbench、MoleculeNet、TDC、OC20/OC22、ORD 均已纳入。
- 输出要求：总览表、研究价值、verifier、输入/输出、open-generation 适合性、评分方式、工程评级、首版优先级、8-12 个首版推荐已给出。
