"""Render the Sage V7.2 knowledge platform architecture diagram."""

from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.generic.compute import Rack
from diagrams.generic.database import SQL
from diagrams.generic.network import Router
from diagrams.generic.storage import Storage
from diagrams.onprem.client import Client, User
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.vcs import Github

OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_BASENAME = OUTPUT_DIR / "sage-knowledge-platform-architecture"

GRAPH_ATTR = {
    "bgcolor": "#F7F9FB",
    "fontname": "PingFang SC",
    "fontsize": "22",
    "pad": "0.35",
    "ranksep": "0.78",
    "nodesep": "0.52",
    "splines": "spline",
}
NODE_ATTR = {
    "fontname": "PingFang SC",
    "fontsize": "12",
    "fontcolor": "#18222B",
    "color": "#D7DEE5",
    "penwidth": "1.2",
}
EDGE_ATTR = {
    "fontname": "PingFang SC",
    "fontsize": "10",
    "fontcolor": "#5F6B76",
    "color": "#8D9AA6",
    "arrowsize": "0.7",
}


with Diagram(
    "Sage V7.2 自我生长知识平台",
    filename=str(OUTPUT_BASENAME),
    outformat=["png", "svg"],
    show=False,
    direction="LR",
    graph_attr=GRAPH_ATTR,
    node_attr=NODE_ATTR,
    edge_attr=EDGE_ATTR,
):
    with Cluster("知识来源"):
        source_local = Storage("本地 / Obsidian")
        source_git = Github("GitHub 仓库")
        source_feishu = Client("飞书文档")
        source_web = Router("网页 / API")

    with Cluster("摄取与理解流水线"):
        adapter = Rack("Source Adapter\n增量扫描 / 去重")
        queue = Redis("Redis Streams\n持久任务队列")
        parser = Rack("Parser / MinerU\n结构与版面解析")
        vision = Rack("Qwen3-VL\n图片 / 表格理解")
        synthesizer = Rack("Wiki Synthesizer\n摘要 / 实体 / 关系")

        adapter >> Edge(label="job + content hash") >> queue
        queue >> Edge(label="claim / retry") >> parser
        parser >> Edge(label="复杂页面") >> vision
        parser >> synthesizer
        vision >> synthesizer

    with Cluster("可审核事实层"):
        originals = Storage("对象存储\n原始文件 / 媒体")
        wiki = Github("Git Wiki\nMarkdown 真相源")
        audit = PostgreSQL("PostgreSQL\n版本 / 来源 / 审计")
        autonomy = Rack("Autonomy Policy\n自动 / 摘要 / 确认 / 阻断")

        synthesizer >> Edge(label="change proposal") >> autonomy
        autonomy >> Edge(label="低风险自动应用") >> wiki
        autonomy >> Edge(label="版本与撤销链") >> audit
        parser >> Edge(label="原件归档") >> originals

    with Cluster("可重建检索投影"):
        fts = PostgreSQL("PostgreSQL FTS\n关键词检索")
        vector = SQL("Qdrant\n向量检索")
        graph = SQL("Graph Tables\n实体 / 关系")
        community = Rack("Louvain\n社区与主题")

        wiki >> Edge(label="chunk + index") >> fts
        wiki >> vector
        wiki >> graph
        graph >> community

    with Cluster("产品与 Agent"):
        rag = Rack("Agentic RAG\nHybrid + RRF + 引用")
        chat = Client("Sage 对话")
        knowledge_ui = Client("Knowledge Workbench")
        hr_agent = User("HR 公开助手\n独立公开知识库")

        fts >> rag
        vector >> rag
        graph >> rag
        community >> knowledge_ui
        rag >> chat
        rag >> knowledge_ui
        rag >> Edge(label="仅已发布资料", color="#E46755") >> hr_agent

    for source in (source_local, source_git, source_feishu, source_web):
        source >> adapter

    originals >> Edge(label="provenance", style="dashed") >> audit
    audit >> Edge(label="审核 / 回滚", style="dashed") >> knowledge_ui
