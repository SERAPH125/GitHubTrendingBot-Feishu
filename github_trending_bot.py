#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Trending 每日推送机器人
功能：每日爬取 GitHub Trending，使用硅基流动 AI 总结，通过飞书机器人推送
部署：GitHub Actions 定时任务（每天早上 8 点）
"""

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from datetime import datetime
import json
import sys

# ==============================================================================
# 配置区域 - 通过环境变量读取
# ==============================================================================

import os

def _env(*names, default=""):
    """读取环境变量；把空字符串视为未设置，避免 Actions 注入空 Secret 覆盖默认值。"""
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


# AI API 配置（兼容硅基流动 / DeepSeek 官方）
# 优先读 DEEPSEEK_API_KEY；也可用 SILICONFLOW_API_KEY
SILICONFLOW_API_KEY = _env("DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY")
_use_deepseek = bool(_env("DEEPSEEK_API_KEY"))
_default_base = "https://api.deepseek.com" if _use_deepseek else "https://api.siliconflow.cn/v1"
# DeepSeek 当前要求 deepseek-v4-flash / deepseek-v4-pro（旧 deepseek-chat 已不可用）
_default_model = "deepseek-v4-flash" if _use_deepseek else "deepseek-ai/DeepSeek-V3"
SILICONFLOW_BASE_URL = _env("SILICONFLOW_BASE_URL", "DEEPSEEK_BASE_URL", default=_default_base)
SILICONFLOW_MODEL = _env("SILICONFLOW_MODEL", "DEEPSEEK_MODEL", default=_default_model)
SILICONFLOW_TIMEOUT = int(_env("SILICONFLOW_TIMEOUT", default="60"))

# 飞书机器人配置
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
FEISHU_MESSAGE_TYPE = "interactive"  # 使用富文本卡片

# GitHub Trending 配置
GITHUB_TRENDING_URL = "https://github.com/trending"
GITHUB_SINCE = os.getenv("GITHUB_SINCE", "daily")  # daily, weekly, monthly
GITHUB_LANGUAGE = os.getenv("GITHUB_LANGUAGE", "")  # 空字符串表示所有语言

# 爬虫配置
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))  # 请求超时时间（秒）
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))  # 最大重试次数
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "5"))  # 重试间隔（秒）

# 日志配置
LOG_ENABLED = os.getenv("LOG_ENABLED", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")  # DEBUG, INFO, WARNING, ERROR

# ==============================================================================
# 环境变量验证
# ==============================================================================

def validate_env():
    """验证必要的环境变量是否配置"""
    errors = []

    if not SILICONFLOW_API_KEY:
        errors.append("未配置 DEEPSEEK_API_KEY 或 SILICONFLOW_API_KEY 环境变量")

    if not FEISHU_WEBHOOK_URL:
        errors.append("未配置 FEISHU_WEBHOOK_URL 环境变量")

    if errors:
        log("环境变量配置错误：", "ERROR")
        for error in errors:
            log(f"  - {error}", "ERROR")
        log("请检查 GitHub Secrets 配置", "ERROR")
        sys.exit(1)

    log("环境变量验证通过")

# ==============================================================================
# 工具函数
# ==============================================================================

def log(message, level="INFO"):
    """输出日志"""
    if LOG_ENABLED:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}", flush=True)

def format_number(num_str):
    """格式化数字（例如：1.2k -> 1200）"""
    num_str = num_str.strip()
    if not num_str:
        return 0

    num_str = num_str.replace(',', '').replace('k', '000').replace('K', '000')

    try:
        # 处理小数点，例如：1.2k -> 1200
        if '.' in num_str:
            parts = num_str.split('.')
            if len(parts) == 2 and parts[1] == '000':
                return int(float(num_str))
        return int(num_str)
    except:
        return 0

def format_stars(stars):
    """格式化星数显示"""
    if stars >= 1000:
        return f"{stars / 1000:.1f}k"
    return str(stars)

# ==============================================================================
# 爬虫模块 - GitHub Trending
# ==============================================================================

class GitHubTrendingCrawler:
    """GitHub Trending 爬虫"""

    def __init__(self):
        self.url = GITHUB_TRENDING_URL
        self.since = GITHUB_SINCE
        self.language = GITHUB_LANGUAGE
        self.timeout = REQUEST_TIMEOUT
        self.max_retries = MAX_RETRIES
        self.retry_delay = RETRY_DELAY

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }

    def fetch_readme(self, repo_url):
        """获取项目的 README.md 内容"""
        readme_urls = [
            f"{repo_url}/blob/main/README.md",
            f"{repo_url}/blob/master/README.md"
        ]

        for readme_url in readme_urls:
            try:
                response = requests.get(readme_url, headers=self.headers, timeout=20)
                response.raise_for_status()

                # 解析 HTML 提取 README 内容
                soup = BeautifulSoup(response.text, 'lxml')
                readme_div = soup.find('div', {'data-testid': 'raw-content'}) or soup.find('article')

                if readme_div:
                    return readme_div.get_text().strip()[:5000]  # 限制长度

            except Exception as e:
                log(f"获取 README 失败 {readme_url}：{str(e)}", "DEBUG")
                continue

        return ""

    def fetch_trending(self):
        """爬取 GitHub Trending 数据"""
        log("开始爬取 GitHub Trending...")

        for attempt in range(self.max_retries):
            try:
                params = {'since': self.since}
                if self.language:
                    params['language'] = self.language

                log(f"网页爬取（尝试 {attempt + 1}/{self.max_retries}）...")

                response = requests.get(
                    self.url,
                    params=params,
                    headers=self.headers,
                    timeout=self.timeout
                )
                response.raise_for_status()

                repos = self._parse_html(response.text)
                log(f"成功爬取 {len(repos)} 个仓库")
                return repos

            except requests.exceptions.Timeout:
                log(f"请求超时（尝试 {attempt + 1}/{self.max_retries}）", "ERROR")
            except requests.exceptions.ConnectionError:
                log(f"连接失败（尝试 {attempt + 1}/{self.max_retries}）", "ERROR")
            except Exception as e:
                log(f"爬取失败（尝试 {attempt + 1}/{self.max_retries}）：{str(e)}", "ERROR")

            # 最后一次尝试不等待
            if attempt < self.max_retries - 1:
                import time
                # 指数退避策略
                wait_time = self.retry_delay * (2 ** attempt)
                log(f"等待 {wait_time} 秒后重试...", "INFO")
                time.sleep(wait_time)

        log("所有爬取尝试均失败", "ERROR")
        return []

    def _parse_html(self, html):
        """解析 HTML 提取仓库信息"""
        soup = BeautifulSoup(html, 'lxml')
        repos = []

        repo_articles = soup.find_all('article', class_='Box-row')

        for article in repo_articles:
            try:
                repo = self._extract_repo_info(article)
                if repo:
                    repos.append(repo)
            except Exception as e:
                log(f"解析仓库信息失败：{str(e)}", "WARNING")
                continue

        return repos

    def _extract_repo_info(self, article):
        """提取单个仓库的信息"""
        # 仓库名称和链接
        title_element = article.find('h2', class_='h3')
        if not title_element:
            return None

        link_element = title_element.find('a')
        if not link_element:
            return None

        repo_name = link_element.get_text().strip().replace('\n', '').replace(' ', '')
        repo_url = 'https://github.com' + link_element.get('href', '')

        # 提取作者和项目名
        if '/' in repo_name:
            parts = repo_name.split('/')
            author = parts[0]
            project_name = parts[1] if len(parts) > 1 else repo_name
        else:
            author = ""
            project_name = repo_name

        # 描述
        desc_element = article.find('p', class_='col-9')
        description = desc_element.get_text().strip() if desc_element else ""

        # 编程语言
        language_element = article.find('span', itemprop='programmingLanguage')
        language = language_element.get_text().strip() if language_element else ""

        # 星数
        stars_element = article.find('a', href=lambda x: x and '/stargazers' in x)
        stars = 0
        if stars_element:
            stars_text = stars_element.get_text().strip()
            stars = format_number(stars_text)

        # Fork 数
        forks_element = article.find('a', href=lambda x: x and '/forks' in x)
        forks = 0
        if forks_element:
            forks_text = forks_element.get_text().strip()
            forks = format_number(forks_text)

        # 今日星数增长
        today_stars_element = article.find('span', class_='d-inline-block float-sm-right')
        today_stars = 0
        if today_stars_element:
            today_stars_text = today_stars_element.get_text().strip()
            if 'stars today' in today_stars_text:
                today_stars = format_number(today_stars_text.split('stars')[0].strip())

        return {
            'name': repo_name,
            'author': author,
            'project_name': project_name,
            'url': repo_url,
            'description': description,
            'language': language,
            'stars': stars,
            'forks': forks,
            'today_stars': today_stars,
            'formatted_stars': format_stars(stars),
            'formatted_today_stars': format_stars(today_stars)
        }
# ==============================================================================
# AI 分析模块 - 硅基流动 API
# ==============================================================================

class SiliconFlowSummarizer:
    """DeepSeek / 硅基流动 AI 分析器：批量生成中文简介与亮点。"""

    def __init__(self):
        self.api_key = SILICONFLOW_API_KEY
        self.base_url = SILICONFLOW_BASE_URL
        self.model = SILICONFLOW_MODEL
        self.timeout = SILICONFLOW_TIMEOUT
        log(f"AI 配置: base_url={self.base_url} model={self.model}")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def analyze_repos(self, repos, limit=10, crawler=None):
        """一次请求批量分析 Top N，避免逐条失败退回英文原文。"""
        del crawler  # 热榜简介足够，不再逐条拉 README（慢且易 404）
        repos_to_analyze = repos[:limit]
        log(f"开始批量中文分析 {len(repos_to_analyze)} 个项目...")

        try:
            analyses = self._analyze_batch(repos_to_analyze)
        except Exception as e:
            log(f"批量分析失败，将逐条重试：{e}", "ERROR")
            analyses = {}
            for repo in repos_to_analyze:
                try:
                    analyses[repo["name"]] = self._analyze_one(repo)
                except Exception as one_err:
                    log(f"项目分析失败 {repo['name']}：{one_err}", "ERROR")
                    analyses[repo["name"]] = self._fallback(repo)

        for repo in repos_to_analyze:
            repo["ai_analysis"] = analyses.get(repo["name"]) or self._fallback(repo)
            desc = repo["ai_analysis"].get("intro") or repo["ai_analysis"].get("chinese_description", "")
            # 若仍像英文原文，标出来方便排查
            if desc and desc.strip() == (repo.get("description") or "").strip()[:120]:
                log(f"警告：{repo['name']} 中文摘要疑似未生效", "WARNING")

        log(f"批量分析完成，共分析 {len(repos_to_analyze)} 个项目")
        return repos_to_analyze

    CATEGORY_CHOICES = [
        "🤖 AI 智能体与大模型生态",
        "🛠️ 开发者工具与基础设施",
        "📈 量化金融与数据引擎",
        "🎨 设计创作与多媒体",
        "🧩 其它值得一看",
    ]

    def _fallback(self, repo):
        raw = (repo.get("description") or "").strip()
        return {
            "category": "🧩 其它值得一看",
            "intro": (
                f"官方简介：{raw[:100]}。当前缺少更细中文解读，建议点进仓库看 README。"
                if raw
                else "暂无项目简介，建议点进仓库查看 README。"
            ),
            "highlight": "今日登上 GitHub 热榜，可先收藏再细看。",
            "scenario": "适合想快速了解该开源方向的开发者浏览试用。",
        }

    def _normalize_analysis(self, item, repo=None):
        intro = (
            item.get("intro")
            or item.get("chinese_description")
            or item.get("summary")
            or item.get("简介")
            or ""
        ).strip()
        highlight = (
            item.get("highlight")
            or item.get("亮点")
            or item.get("why_hot")
            or ""
        ).strip()
        scenario = (
            item.get("scenario")
            or item.get("应用场景")
            or item.get("audience")
            or ""
        ).strip()
        category = (item.get("category") or item.get("分类") or "").strip()
        if category not in self.CATEGORY_CHOICES:
            category = self._guess_category(repo, intro + highlight + scenario)
        if not intro and repo is not None:
            return self._fallback(repo)
        return {
            "category": category,
            "intro": intro or "暂无简介",
            "highlight": highlight or "今日热榜项目，可点开仓库进一步了解。",
            "scenario": scenario or "适合对该技术领域感兴趣的开发者了解试用。",
            # 兼容旧字段，避免其它逻辑读空
            "chinese_description": intro,
            "audience": scenario,
        }

    def _guess_category(self, repo, text):
        blob = f"{(repo or {}).get('name','')} {(repo or {}).get('description','')} {text}".lower()
        if any(k in blob for k in ("trading", "quant", "stock", "回测", "量化", "a股", "etf", "finance")):
            return "📈 量化金融与数据引擎"
        if any(k in blob for k in ("agent", "llm", "openai", "claude", "gpt", "speech", "ai ", "模型", "智能体")):
            return "🤖 AI 智能体与大模型生态"
        if any(k in blob for k in ("jenkins", "devops", "cli", "infra", "gis", "file manager", "deploy", "docker")):
            return "🛠️ 开发者工具与基础设施"
        if any(k in blob for k in ("design", "video", "image", "3d", "editor", "creative")):
            return "🎨 设计创作与多媒体"
        return "🧩 其它值得一看"

    def _analyze_batch(self, repos):
        """按用户期望格式：分类 + 简介 + 亮点 + 应用场景。"""
        items = []
        for i, repo in enumerate(repos, 1):
            items.append(
                f"{i}. name={repo['name']}\n"
                f"   language={repo.get('language') or 'Unknown'}\n"
                f"   stars={repo.get('formatted_stars')} (+{repo.get('formatted_today_stars')} today)\n"
                f"   description={repo.get('description') or 'N/A'}"
            )
        cats = " / ".join(self.CATEGORY_CHOICES)
        prompt = (
            "下面是今日 GitHub Trending Top 项目。请写成「科技早报」风格的中文解读。\n"
            "读者可能不懂细分术语，要用大白话，像下面这样清楚：\n"
            "简介：吴恩达团队推出的轻量级 Python 库。\n"
            "亮点：统一对接 OpenAI/Anthropic/Google 等多家模型，降低多模型集成门槛。\n"
            "应用场景：要在业务里同时试用多家大模型、并想随时切换供应商时。\n\n"
            "每个项目必须返回字段：\n"
            f"1. category：只能从这些里选一个——{cats}\n"
            "2. intro（简介）：40～90 字。先说「谁做的/什么东西」，再说核心能力。\n"
            "3. highlight（亮点）：40～90 字。写具体能力或差异点，不要空泛夸奖。\n"
            "4. scenario（应用场景）：30～70 字。写「什么人、在什么情况下会用到」。\n\n"
            "硬性要求：全中文；不要照抄英文原句；只返回 JSON 数组；不要 markdown。\n"
            "格式：\n"
            '[{"name":"owner/repo","category":"🤖 AI 智能体与大模型生态",'
            '"intro":"...","highlight":"...","scenario":"..."}]\n\n'
            + "\n".join(items)
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是中文科技媒体编辑，擅长把 GitHub 热榜整理成分类早报："
                        "有简介、有亮点、有应用场景，读完就知道项目能干嘛。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.55,
            max_tokens=5000,
        )
        result_text = response.choices[0].message.content or ""
        log(f"批量分析原始返回长度: {len(result_text)}")
        parsed_list = self._parse_batch_result(result_text)
        by_name = {}
        for item in parsed_list:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            by_name[name] = self._normalize_analysis(item)
        if len(by_name) < max(1, len(repos) // 2):
            raise RuntimeError(f"批量结果过少：仅解析到 {len(by_name)} 条")
        return by_name

    def _analyze_one(self, repo):
        cats = " / ".join(self.CATEGORY_CHOICES)
        prompt = (
            f"项目: {repo['name']}\n语言: {repo.get('language')}\n"
            f"描述: {repo.get('description')}\n\n"
            "请返回 JSON："
            '{"name":"owner/repo","category":"...", "intro":"简介",'
            '"highlight":"亮点","scenario":"应用场景"}。\n'
            f"category 只能是：{cats}\n"
            "风格：科技早报，大白话，具体，不要空夸。"
        )
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "你是中文科技媒体编辑，输出分类早报 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.55,
            max_tokens=700,
        )
        return self._normalize_analysis(
            self._parse_result(response.choices[0].message.content or ""),
            repo=repo,
        )

    def _parse_batch_result(self, result_text):
        try:
            start = result_text.find("[")
            end = result_text.rfind("]")
            if start != -1 and end != -1 and end > start:
                data = json.loads(result_text[start : end + 1])
                if isinstance(data, list):
                    return data
        except Exception as e:
            log(f"批量 JSON 解析失败：{e}", "WARNING")
        # 兼容模型偶发返回 {items:[...]}
        try:
            start = result_text.find("{")
            end = result_text.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(result_text[start : end + 1])
                if isinstance(data, dict):
                    for key in ("items", "repos", "data"):
                        if isinstance(data.get(key), list):
                            return data[key]
        except Exception:
            pass
        return []

    def _parse_result(self, result_text):
        """解析单条 AI 返回"""
        try:
            start_idx = result_text.find("{")
            if start_idx != -1:
                stack = []
                end_idx = start_idx
                for i in range(start_idx, len(result_text)):
                    char = result_text[i]
                    if char == "{":
                        stack.append(char)
                    elif char == "}":
                        if stack:
                            stack.pop()
                            if not stack:
                                end_idx = i + 1
                                break
                if end_idx > start_idx:
                    parsed = json.loads(result_text[start_idx:end_idx])
                    if parsed.get("chinese_description") or parsed.get("summary"):
                        return parsed
        except Exception:
            pass
        return {
            "chinese_description": (result_text or "").strip()[:140] or "暂无中文描述",
            "audience": "对相关技术感兴趣的开发者",
            "highlight": "今日热榜项目",
        }

# ==============================================================================
# 美化模块 - AgentSkills frontend-design
# ==============================================================================

class AgentSkillsBeautifier:
    """AgentSkills 内容美化器"""
    
    def __init__(self):
        self.enabled = True
    
    def beautify(self, repos):
        """使用 AgentSkills 进行内容美化"""
        log("开始使用 AgentSkills 进行内容美化...")
        
        try:
            # 构建 Markdown 格式的美化内容
            date = datetime.now().strftime("%Y-%m-%d")
            
            # 构建 Markdown 内容
            markdown_content = self._build_markdown(repos, date)
            
            log("内容美化完成")
            return markdown_content
            
        except Exception as e:
            log(f"内容美化失败：{str(e)}", "ERROR")
            # 返回降级方案
            return self._fallback_beautify(repos)
    
    def _build_markdown(self, repos, date):
        """按分类输出：简介 / 亮点 / 应用场景。"""
        lines = [f"# 🚀 GitHub 热榜日报 - {date}", ""]
        grouped = {}
        order = list(SiliconFlowSummarizer.CATEGORY_CHOICES)
        for repo in repos:
            ai = repo.get("ai_analysis") or {}
            cat = ai.get("category") or "🧩 其它值得一看"
            grouped.setdefault(cat, []).append(repo)

        for cat in order:
            items = grouped.get(cat) or []
            if not items:
                continue
            lines.append(f"**{cat}**")
            lines.append("")
            for repo in items:
                lines.append(self._build_repo_card(repo))
                lines.append("")
        # 兜底：未识别分类
        for cat, items in grouped.items():
            if cat in order:
                continue
            lines.append(f"**{cat}**")
            lines.append("")
            for repo in items:
                lines.append(self._build_repo_card(repo))
                lines.append("")
        return "\n".join(lines)

    def _build_repo_card(self, repo, index=None):
        """单项目：标题 + 简介 + 亮点 + 应用场景。"""
        del index
        ai = repo.get("ai_analysis") or {}
        intro = (ai.get("intro") or ai.get("chinese_description") or "").strip() or "暂无简介"
        highlight = (ai.get("highlight") or "").strip() or "今日热榜项目"
        scenario = (ai.get("scenario") or ai.get("audience") or "").strip() or "适合相关开发者了解试用"
        # 展示成 owner / repo，更接近早报阅读习惯
        name = repo.get("name") or ""
        pretty = name.replace("/", " / ") if "/" in name else name
        stars = (
            f"⭐ {repo.get('formatted_stars')} · "
            f"{self._get_language_emoji(repo.get('language'))} {repo.get('language') or 'Unknown'} · "
            f"今日+{repo.get('formatted_today_stars')}"
        )
        return "\n".join(
            [
                f"**[{pretty}]({repo.get('url')})**",
                stars,
                f"简介：{intro}",
                f"亮点：{highlight}",
                f"应用场景：{scenario}",
            ]
        )
    
    def _get_language_emoji(self, language):
        """获取编程语言的 emoji"""
        emoji_map = {
            'Python': '🐍',
            'JavaScript': '📜',
            'TypeScript': '📘',
            'Java': '☕',
            'Go': '🐹',
            'Rust': '🦀',
            'C++': '⚡',
            'C': '🔧',
            'PHP': '🐘',
            'Ruby': '💎',
            'Swift': '🦉',
            'Kotlin': '🎯',
            'Dart': '🎯',
            'HTML': '🌐',
            'CSS': '🎨',
            'Vue': '💚',
            'React': '⚛️',
            'Angular': '🅰️',
            'Shell': '💻',
            'Jupyter Notebook': '📓',
        }
        return emoji_map.get(language, '💻')
    
    def _fallback_beautify(self, repos):
        """降级美化方案"""
        date = datetime.now().strftime("%Y-%m-%d")
        
        content = f"🚀 GitHub 热榜日报 - {date}\n\n"
        
        for i, repo in enumerate(repos[:10], 1):
            content += f"{i}. [{repo['name']}]({repo['url']}) by {repo['author']}\n"
            content += f"   ⭐ {repo['formatted_stars']} stars\n"
            content += f"   {self._get_language_emoji(repo['language'])} {repo['language']}\n"
            content += f"   📈 +{repo['formatted_today_stars']} today\n"
            content += "\n"
        
        content += f"---\n📊 数据来源：https://github.com/trending\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return content

# ==============================================================================
# 飞书推送模块
# ==============================================================================

class FeishuNotifier:
    """飞书机器人通知器"""
    
    def __init__(self):
        self.webhook_url = FEISHU_WEBHOOK_URL
        self.message_type = FEISHU_MESSAGE_TYPE
    
    def send(self, content):
        """发送消息到飞书"""
        log("开始发送飞书消息...")
        
        try:
            if self.message_type == "interactive":
                # 发送富文本卡片
                data = self._build_card_message(content)
            else:
                # 发送文本消息
                data = {
                    "msg_type": "text",
                    "content": {
                        "text": content
                    }
                }
            
            response = requests.post(
                self.webhook_url,
                json=data,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            response.raise_for_status()
            result = response.json()
            
            if result.get('StatusCode') == 0 or result.get('code') == 0:
                log("飞书消息发送成功")
                return True
            else:
                log(f"飞书消息发送失败：{result}", "ERROR")
                return False
                
        except Exception as e:
            log(f"飞书消息发送异常：{str(e)}", "ERROR")
            return False
    
    def _build_card_message(self, markdown_content):
        """构建富文本卡片消息（Top10 全量中文解读）"""
        title = "🚀 GitHub 热榜早报（分类解读）"
        body = markdown_content.strip()
        body += "\n\n---\n📊 数据：github.com/trending · 🧠 DeepSeek 白话解读"

        # 飞书单段 lark_md 过长易失败：按项目块拆成多段
        chunks = self._split_markdown_chunks(body, max_len=1800)
        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": chunk}}
            for chunk in chunks
        ]
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": elements,
        }
        return {"msg_type": "interactive", "card": card}

    def _split_markdown_chunks(self, text, max_len=1800):
        """按空行块切分，避免单卡片超长。"""
        blocks = text.split("\n\n")
        chunks = []
        current = []
        current_len = 0
        for block in blocks:
            block_len = len(block) + 2
            if current and current_len + block_len > max_len:
                chunks.append("\n\n".join(current))
                current = [block]
                current_len = len(block)
            else:
                current.append(block)
                current_len += block_len
        if current:
            chunks.append("\n\n".join(current))
        return chunks or [text[:max_len]]

# ==============================================================================
# 主程序
# ==============================================================================

def main():
    """主程序入口"""
    log("=" * 60)
    log("GitHub Trending 每日推送机器人启动")
    log("=" * 60)

    # 验证环境变量
    validate_env()
    log(f"运行配置: model={SILICONFLOW_MODEL} base={SILICONFLOW_BASE_URL}")

    try:
        # 1. 爬取 GitHub Trending
        crawler = GitHubTrendingCrawler()
        repos = crawler.fetch_trending()
        
        if not repos:
            log("未获取到仓库数据，程序终止", "ERROR")
            sys.exit(1)
        
        # 2. AI 中文批量分析 Top 10（不再逐条拉 README）
        summarizer = SiliconFlowSummarizer()
        analyzed_repos = summarizer.analyze_repos(repos, limit=10)
        
        # 3. 内容美化
        beautifier = AgentSkillsBeautifier()
        beautified_content = beautifier.beautify(analyzed_repos)
        
        # 4. 飞书推送
        notifier = FeishuNotifier()
        success = notifier.send(beautified_content)
        
        if success:
            log("=" * 60)
            log("✅ GitHub Trending 推送成功！")
            log("=" * 60)
            sys.exit(0)
        else:
            log("=" * 60)
            log("❌ GitHub Trending 推送失败！")
            log("=" * 60)
            sys.exit(1)
            
    except Exception as e:
        log(f"程序执行出错：{str(e)}", "ERROR")
        import traceback
        log(traceback.format_exc(), "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()