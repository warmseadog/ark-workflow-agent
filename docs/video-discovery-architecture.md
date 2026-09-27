# 女装穿搭视频发现与二创流水线设计

## 目标

将“寻找女装穿搭博主视频”拆成一个可复用的发现服务，并把通过人工审核的候选视频接入现有的人脸打码、服装替换和视频生成流程。

## 结论

RAG 不应承担平台检索和下载。平台检索由来源适配器负责，RAG 只用于读取公司历史案例、扩展查询词、解释排序结果和复用成功经验。Skill 负责把这些能力编排成可调用的工作流，核心数据处理仍放在可测试的服务代码中。

## 分层架构

```text
用户输入主题/参考图
        |
        v
案例库 RAG --> 查询计划器 --> 来源适配器（抖音 / 小红书 / 人工链接）
                              |
                              v
                    归一化、去重、合规检查
                              |
                              v
                    视觉与热度特征抽取
                              |
                              v
                    多目标排序 + 人工审核
                              |
                              v
      下载/引用 --> 人脸打码 --> 服装/参考图替换 --> 视频模型 --> 质检
```

### 1. 查询计划器

输入包括品类、季节、风格、目标人群、镜头偏好和参考图。计划器从案例库检索相似成功案例，生成 3 类查询：风格词、单品词、场景词。每类保留同义词和平台常用标签，便于不同来源适配器分别执行。

### 2. 来源适配器

所有平台都实现同一接口，先支持人工粘贴链接和公开搜索结果，再逐步接入公司已经获授权的来源。适配器只返回标准化元数据，不把平台页面结构泄漏到后续模块。

```ts
export type VideoCandidate = {
  source: 'douyin' | 'xiaohongshu' | 'manual';
  sourceId?: string;
  canonicalUrl: string;
  title?: string;
  author?: string;
  publishedAt?: string;
  metrics?: { likes?: number; comments?: number; shares?: number; views?: number };
  coverUrl?: string;
  mediaUrl?: string;
  tags: string[];
  rights: { status: 'unknown' | 'review' | 'cleared' | 'blocked'; evidence?: string };
  raw: Record<string, unknown>;
};

export interface VideoSourceAdapter {
  search(plan: QueryPlan): Promise<VideoCandidate[]>;
  getByUrl(url: string): Promise<VideoCandidate | null>;
}
```

适配器必须遵守平台条款和公司的授权范围；不能把绕过登录、验证码、访问控制或版权限制作为系统能力。没有稳定接口时，MVP 采用人工粘贴链接、浏览器导入或公司已有合规采集服务的结果。

### 3. 归一化与去重

用平台 ID、规范化 URL、封面感知哈希和视频首帧指纹做四级去重。保留原始字段，避免后续排序或审计时丢失证据。

### 4. 生产适配评分

第一版用可解释的固定权重，不要一开始训练复杂模型：

```text
总分 = 0.30 * 主题相关度
     + 0.20 * 趋势分
     + 0.20 * 画面可编辑性
     + 0.15 * 案例相似度
     + 0.10 * 权利可用度
     + 0.05 * 处理成本分
```

“画面可编辑性”由人脸可定位率、人物占比、镜头切换密度、遮挡比例、服装区域面积和视频清晰度组成。权利未知的候选只进入审核队列，不自动进入生成。

### 5. 审核与流水线衔接

审核卡片至少展示封面、来源、作者、热度、命中查询、案例解释、权利状态和预估处理成本。审核通过后创建 `video_job`，把候选的规范化 ID 传给现有打码服务；打码服务完成后，再把 `face_mask_asset_id` 和参考服装图传给换装/视频模型步骤。

```ts
type VideoJob = {
  candidateId: string;
  referenceImageId: string;
  steps: Array<'download' | 'face_blur' | 'garment_replace' | 'video_generate' | 'qc'>;
  status: 'pending' | 'running' | 'failed' | 'completed';
};
```

## RAG、Skill 和普通代码的边界

| 能力 | 放在哪里 | 作用 |
| --- | --- | --- |
| 平台搜索、链接解析、去重、特征抽取、排序 | 普通服务代码 | 可测试、可重试、可观测 |
| 公司成功案例、风格词、失败原因、排序解释 | 向量库 + 结构化字段 | 查询扩展、相似案例、结果解释 |
| “为某主题找 20 条候选并进入审核” | Skill/工作流 | 串联工具，不承载平台细节 |
| 人脸打码与视频模型调用 | 现有媒体流水线 | 复用已有能力，按 job 状态推进 |

案例库不要只存视频向量，还要存结构化标签：风格、单品、镜头模板、人物姿态、时长、平台、最终效果、失败原因和是否可复用。这样检索结果既能用于语义相似，也能按条件过滤。

## 推荐的 MVP 顺序

1. **人工链接导入**：先做标准化、去重、评分和审核页面，验证排序是否能找到可制作素材。
2. **公开搜索适配器**：输出候选元数据和封面，不自动下载；验证查询计划和案例 RAG 的命中率。
3. **授权来源接入**：把公司已有的合规采集或合作方结果接到同一适配器接口。
4. **自动特征抽取**：加入 ASR、OCR、镜头切分、人脸/人体检测和清晰度评分。
5. **学习排序**：积累审核通过、生成成功和发布效果后，再训练轻量 reranker。

第一步就能接入当前的打码环节，也能避免先投入一个无法验证的平台爬虫。建议先把“发现”和“生产”之间固定成 `candidate -> review -> video_job` 三个状态边界。

## 建议目录

```text
src/
  discovery/
    adapters/
      manual.ts
      douyin.ts
      xiaohongshu.ts
    normalize.ts
    dedupe.ts
    features.ts
    rank.ts
    types.ts
  cases/
    retriever.ts
    query-planner.ts
  jobs/
    video-job.ts
    state-machine.ts
  api/
    discovery-routes.ts
```

如果当前项目只有前端 `index.html`，建议先在前端增加“粘贴链接/搜索词/候选审核”界面，后端再按上面的接口拆出服务；不要在浏览器里直接保存平台 Cookie、下载受限媒体或执行不可审计的采集脚本。
