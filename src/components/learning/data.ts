export type Version = "original" | "recitation" | "keywords";

export const versionContract = [
  { id: "original", label: "原文版本" },
  { id: "recitation", label: "背诵版本" },
  { id: "keywords", label: "关键词版本" },
] as const satisfies readonly { id: Version; label: string }[];

export const versionLabels = Object.fromEntries(
  versionContract.map(({ id, label }) => [id, label]),
) as Record<Version, string>;

export type VersionPoint = { title: string; content: string[] };

export type PointVersions = Record<Version, VersionPoint>;

export type BulletPoint = {
  id: string;
  versions: PointVersions;
};

export type KnowledgePoint = {
  id: string;
  title: string;
  bulletPoints: BulletPoint[];
};

export type Chapter = {
  id: string;
  number: string | null;
  title: string | null;
  points: KnowledgePoint[];
};

export type Project = {
  id: string;
  name: string;
  meta: string;
  documentTitle: string;
  chapters: Chapter[];
};

const rawProjects = [
  {
    id: "economics",
    name: "西方经济学",
    meta: "期末复习",
    documentTitle: "市场失灵与宏观经济政策",
    chapters: [
      {
        id: "eco-market-failure", number: "03", title: "市场失灵与政府干预",
        points: [
          {
            id: "eco-externality", title: "外部性及其影响",
            versions: {
              original: ["外部性是指一个经济主体的行为对其他主体产生了影响，但这种影响没有通过市场价格得到反映。当生产者或消费者的行为给他人带来未被补偿的成本时，称为负外部性；当其行为给他人带来未获得报酬的收益时，称为正外部性。", "外部性的存在意味着私人成本与社会成本，或私人收益与社会收益之间出现偏离。市场参与者依据私人边际成本和私人边际收益作出决策，因而市场均衡数量通常不会等于社会最优数量。"],
              recitation: ["外部性是经济主体的活动对第三方产生未通过价格机制反映的影响。它分为正外部性与负外部性。", "外部性使私人成本与社会成本、私人收益与社会收益不一致，导致市场配置偏离社会最优水平，因此构成市场失灵的重要原因。"],
              keywords: ["价格机制之外的影响", "私人成本与社会成本", "正外部性 / 负外部性"],
            },
          },
          {
            id: "eco-public-goods", title: "公共物品",
            versions: {
              original: ["公共物品同时具有非竞争性与非排他性。非竞争性意味着一个人对物品的使用不会减少其他人可使用的数量；非排他性意味着很难或成本很高地阻止未付费者使用该物品。", "由于非排他性，个人可能隐瞒自己的真实支付意愿并等待他人提供公共物品，由此产生搭便车问题。私人市场难以获得足够收入，公共物品通常会出现供给不足。"],
              recitation: ["公共物品具有非竞争性和非排他性。非竞争性指增加一名使用者的边际成本接近于零；非排他性指难以排除未付费者。", "非排他性会引发搭便车问题，使私人供给者无法收回成本，最终造成公共物品供给不足，需要政府或集体机制介入。"],
              keywords: ["非竞争性", "非排他性", "搭便车"],
            },
          },
        ],
      },
      {
        id: "eco-macro-policy", number: "04", title: "宏观经济政策",
        points: [{
          id: "eco-fiscal-policy", title: "财政政策的作用机制",
          versions: {
            original: ["财政政策是政府通过调整支出与税收影响总需求，进而调节产出、就业和价格水平的政策。当经济衰退时，政府可以增加购买支出或减少税收以扩大总需求；经济过热时则采取相反措施。", "财政政策的实际效果取决于乘数大小、政策时滞、经济所处阶段以及货币政策的配合。政府支出增加也可能推高利率，并对私人投资产生一定挤出效应。"],
            recitation: ["财政政策通过政府支出与税收调节总需求。扩张性财政政策增加支出或减少税收；紧缩性财政政策减少支出或增加税收。", "其效果受到乘数、政策时滞和挤出效应影响。判断政策效果时，需要结合经济周期与货币政策环境。"],
            keywords: ["政府支出与税收", "扩张 / 紧缩", "乘数与挤出效应"],
          },
        }],
      },
    ],
  },
  {
    id: "management",
    name: "管理学原理",
    meta: "考试复习",
    documentTitle: "组织与管理的基本原理",
    chapters: [
      {
        id: "mgmt-foundation", number: "01", title: "管理活动与管理者",
        points: [
          { id: "mgmt-functions", title: "管理的基本职能", versions: { original: ["管理是组织为了有效实现目标，对人力、物力、财力和信息等资源进行计划、组织、领导与控制的过程。各项职能相互联系，形成持续循环。", "计划确定目标和路径，组织配置资源，领导影响成员行为，控制则比较实际结果与既定标准并采取修正措施。"], recitation: ["管理的四项基本职能是计划、组织、领导和控制。计划回答做什么，组织回答由谁做，领导推动成员行动，控制保证结果符合目标。"], keywords: ["计划", "组织", "领导", "控制"] } },
          { id: "mgmt-skills", title: "管理者的技能", versions: { original: ["管理者需要技术技能、人际技能与概念技能。不同管理层级对三类技能的要求有所不同，但人际技能对各层级管理者都十分重要。"], recitation: ["管理者技能包括技术技能、人际技能和概念技能。基层更重技术技能，高层更重概念技能，人际技能贯穿所有层级。"], keywords: ["技术技能", "人际技能", "概念技能"] } },
        ],
      },
      { id: "mgmt-decision", number: "02", title: "决策与计划", points: [{ id: "mgmt-decision-process", title: "决策过程", versions: { original: ["决策通常从识别问题开始，经过确定标准、拟定与评价方案、选择方案、实施方案，最终对结果进行评估。"], recitation: ["决策过程包括识别问题、确定标准、拟定方案、评价选择、组织实施和结果评估。"], keywords: ["识别问题", "评价方案", "实施与评估"] } }] },
    ],
  },
  {
    id: "history",
    name: "中国近现代史",
    meta: "课程复习",
    documentTitle: "近代中国的社会变迁",
    chapters: [
      {
        id: "history-opening", number: "01", title: "近代中国的开端",
        points: [
          { id: "history-opium-war", title: "鸦片战争的影响", versions: { original: ["鸦片战争后，中国社会性质和主要矛盾开始发生深刻变化。外国资本主义的入侵破坏了中国的领土主权与经济结构，也促使传统社会逐步转型。"], recitation: ["鸦片战争成为中国近代史的开端。战后中国逐步由封建社会转变为半殖民地半封建社会，社会主要矛盾发生变化。"], keywords: ["近代史开端", "社会性质变化", "主要矛盾"] } },
          { id: "history-treaty", title: "不平等条约体系", versions: { original: ["一系列不平等条约使列强取得割地、赔款、协定关税和领事裁判权等特权，中国主权受到持续损害。"], recitation: ["不平等条约体系通过割地、赔款、协定关税和领事裁判权等内容，系统损害中国主权。"], keywords: ["割地赔款", "协定关税", "领事裁判权"] } },
        ],
      },
      { id: "history-exploration", number: "02", title: "国家出路的早期探索", points: [{ id: "history-westernization", title: "洋务运动", versions: { original: ["洋务派以自强、求富为口号，兴办近代军事工业、民用企业和新式学堂，在客观上推动了中国早期工业与教育发展。"], recitation: ["洋务运动以自强、求富为目标，创办近代企业和新式教育，但未改变封建制度，最终未能实现富国强兵。"], keywords: ["自强求富", "近代企业", "未改变制度"] } }] },
    ],
  },
];

export const initialProjects: Project[] = rawProjects.map((project) => ({
  ...project,
  chapters: project.chapters.map((chapter) => ({
    ...chapter,
    points: chapter.points.map((point) => ({
      id: `knowledge-${point.id}`,
      title: point.title,
      bulletPoints: [{
        id: point.id,
        versions: {
          original: { title: point.title, content: point.versions.original },
          recitation: { title: point.title, content: point.versions.recitation },
          keywords: { title: point.title, content: point.versions.keywords },
        },
      }],
    })),
  })),
}));
