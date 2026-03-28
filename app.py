#!/usr/bin/env python3
"""
新闻展示页面服务 - 修复版 v4
修复：东方财富、新浪财经、财联社抓取
"""

import os
import sys
import json
import re
import hashlib
import threading
import time
from flask import Flask, render_template_string, jsonify, request, make_response
from datetime import datetime, timedelta
from html.parser import HTMLParser

# 尝试导入 requests
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    import urllib.request
    import ssl
    REQUESTS_AVAILABLE = False

app = Flask(__name__)

# 添加 CORS 支持
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, API_SECRET, Api-Secret, api-secret')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    return response

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_API_BASE = os.getenv('DEEPSEEK_API_BASE', 'https://api.deepseek.com')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')

# API 接口认证配置
API_SECRET = os.getenv('API_SECRET', 'finnews-api-secret-2026')

# 缓存配置
CACHE_DIR = '/opt/finnews/.cache'
TRANSLATION_CACHE_DIR = '/opt/finnews/.cache/translations'
CACHE_TTL_HOURS = 24
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(TRANSLATION_CACHE_DIR, exist_ok=True)

# 全局缓存
_global_news_cache = {'data': None, 'timestamp': None, 'lock': threading.Lock()}

# 定时任务配置
AUTO_REFRESH_INTERVAL_HOURS = 6  # 每6小时自动刷新
_last_auto_refresh = None
_auto_refresh_lock = threading.Lock()

# 关键词库
ALL_KEYWORDS = ['股市', 'A股', '港股', '美股', '大盘', '指数', '银行', '保险', '证券', '地产', '医药', '科技', '芯片', '半导体', '新能源', '光伏', '锂电池', '电动车', '人工智能', 'AI', '军工', '消费', '白酒', '上市公司', '财报', 'IPO', '央行', '美联储', '降准', '降息', '基金', '股票', '利好', '利空', '战争', '冲突', '军事', '地缘政治']


# ==================== 多语言支持 ====================
TRANSLATIONS = {
    'zh': {
        'title': '财经新闻',
        'subtitle': 'AI 智能分析',
        'update_time': '更新时间',
        'news_count': '共',
        'count_suffix': '条',
        'finance_news': '财经要闻',
        'weibo_hot': '微博热搜 · 股市相关',
        'weibo_empty': '当前微博热搜暂无直接影响股市的内容',
        'weibo_analyzed': 'DeepSeek 已分析前20条热搜',
        'summary': '新闻摘要',
        'weibo_summary': '热搜摘要',
        'ai_analysis': 'AI 智能分析',
        'market_impact': '市场影响',
        'investment_direction': '投资方向',
        'rank': '热度排名',
        'rank_suffix': '名',
        'read_more': '阅读全文',
        'view_hot': '查看热搜',
        'loading': '加载中...',
        'switch_lang': 'English',
        'positive': '正面',
        'negative': '负面',
        'neutral': '中性',
        'analyzing': '分析中...',
        'follow': '建议关注',
        'comprehensive': '综合',
        'hot_search': '热搜',
    },
    'en': {
        'title': 'Financial News',
        'subtitle': 'AI Powered Analysis',
        'update_time': 'Updated',
        'news_count': '',
        'count_suffix': 'articles',
        'finance_news': 'Financial News',
        'weibo_hot': 'Weibo Hot Search · Stock Related',
        'weibo_empty': 'No stock-related content in current Weibo hot search',
        'weibo_analyzed': 'DeepSeek analyzed top 20 hot searches',
        'summary': 'Summary',
        'weibo_summary': 'Hot Search Summary',
        'ai_analysis': 'AI Analysis',
        'market_impact': 'Market Impact',
        'investment_direction': 'Investment Direction',
        'rank': 'Rank',
        'rank_suffix': '',
        'read_more': 'Read More',
        'view_hot': 'View Hot Search',
        'loading': 'Loading...',
        'switch_lang': '中文',
        'positive': 'Positive',
        'negative': 'Negative',
        'neutral': 'Neutral',
        'analyzing': 'Analyzing...',
        'follow': 'Watch',
        'comprehensive': 'General',
        'hot_search': 'HOT',
    }
}

def get_text(key, lang='zh'):
    return TRANSLATIONS.get(lang, TRANSLATIONS['zh']).get(key, key)

# ==================== 翻译缓存功能 ====================
def get_translation_cache_key(text, lang):
    return hashlib.md5(f"{text}:{lang}".encode('utf-8')).hexdigest()

def get_cached_translation(text, lang):
    if not text or lang == 'zh':
        return None
    key = get_translation_cache_key(text, lang)
    path = os.path.join(TRANSLATION_CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        cached_time = datetime.fromisoformat(cached['cached_at'])
        if datetime.now() - cached_time > timedelta(hours=CACHE_TTL_HOURS * 7):
            os.remove(path)
            return None
        return cached['translation']
    except:
        return None

def set_cached_translation(text, lang, translation):
    if not text or not translation or lang == 'zh':
        return
    key = get_translation_cache_key(text, lang)
    path = os.path.join(TRANSLATION_CACHE_DIR, f"{key}.json")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({
                'cached_at': datetime.now().isoformat(),
                'original': text,
                'translation': translation,
                'lang': lang
            }, f, ensure_ascii=False)
    except:
        pass

def translate_text(text, target_lang='en'):
    if not text or target_lang == 'zh':
        return text
    cached = get_cached_translation(text, target_lang)
    if cached:
        return cached
    if not DEEPSEEK_API_KEY:
        return text
    try:
        prompt = f"""请将以下中文翻译成英文，保持简洁专业：

{text}

直接输出英文翻译，不要添加任何解释："""
        headers = {
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json'
        }
        data = {
            'model': DEEPSEEK_MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 500,
            'temperature': 0.3
        }
        resp = requests.post(
            f'{DEEPSEEK_API_BASE}/chat/completions',
            headers=headers,
            json=data,
            timeout=10
        )
        if resp.status_code == 200:
            result = resp.json()
            translation = result['choices'][0]['message']['content'].strip()
            set_cached_translation(text, target_lang, translation)
            return translation
    except Exception as e:
        print(f"[Translate] Error: {e}")
    return text

def translate_news_item(news, lang='en'):
    if lang == 'zh':
        return news
    translated = news.copy()
    
    # 翻译标题
    if news.get('title'):
        translated['title'] = translate_text(news['title'], lang)
    
    # 翻译来源
    source_map = {
        '东方财富': 'Eastmoney',
        '新浪财经': 'Sina Finance',
        '财联社': 'CLS',
        '微博热搜': 'Weibo Hot'
    }
    if news.get('source'):
        translated['source'] = source_map.get(news['source'], news['source'])
    
    # 翻译AI摘要
    if news.get('ai_summary') and news['ai_summary'] != '摘要生成中...':
        translated['ai_summary'] = translate_text(news['ai_summary'], lang)
    
    # 翻译影响分析
    if news.get('impact') and news['impact'] != '分析中...':
        translated['impact'] = translate_text(news['impact'], lang)
    
    # 翻译趋势/投资方向
    if news.get('trend') and news['trend'] != '建议关注':
        translated['trend'] = translate_text(news['trend'], lang)
    
    # 翻译行业标签
    if news.get('industries'):
        industry_map = {
            '综合': 'General', '科技': 'Technology', '金融': 'Finance',
            '医药': 'Healthcare', '消费': 'Consumer', '能源': 'Energy',
            '地产': 'Real Estate', '军工': 'Defense', '汽车': 'Automotive',
            '人工智能': 'AI', '芯片': 'Semiconductor', '半导体': 'Semiconductor',
            '新能源': 'New Energy',
            '银行': 'Banking', '保险': 'Insurance', '证券': 'Securities',
            '相关板块（未具体指明）': 'Related Sectors',
            '锂电池': 'Lithium Battery', '锂电': 'Lithium Battery', '锂矿': 'Lithium Mining',
            '光伏': 'Solar PV', '电动车': 'EV',
            '有色金属': 'Non-ferrous Metals', '钢铁': 'Steel', '煤炭': 'Coal',
            '石油': 'Oil', '化工': 'Chemicals', '电力': 'Power',
            '通信': 'Telecom', '互联网': 'Internet', '传媒': 'Media',
            '电子': 'Electronics', '计算机': 'Computer', '机械设备': 'Machinery',
            '建筑': 'Construction', '建材': 'Building Materials', '交通运输': 'Transportation',
            '农林牧渔': 'Agriculture', '食品饮料': 'Food & Beverage', '家用电器': 'Home Appliances',
            '纺织服装': 'Textile & Apparel', '轻工制造': 'Light Manufacturing',
            '商业贸易': 'Commerce', '休闲服务': 'Leisure Services', '公用事业': 'Utilities',
            '环保': 'Environmental', '物流': 'Logistics', '零售': 'Retail',
            '教育': 'Education', '旅游': 'Tourism', '酒店': 'Hotel',
            '游戏': 'Gaming', '影视': 'Film & TV', '广告': 'Advertising',
            '体育': 'Sports', '养老': 'Elderly Care', '医疗': 'Medical',
            '生物科技': 'Biotech', '创新药': 'Innovative Drugs', '医疗器械': 'Medical Devices',
            '中药': 'Traditional Chinese Medicine', 'CXO': 'CXO', '医疗服务': 'Medical Services',
            '保险Ⅱ': 'Insurance', '证券Ⅱ': 'Securities', '银行Ⅱ': 'Banking',
            '多元金融': 'Diversified Finance', '金融科技': 'FinTech', '互联网金融': 'Internet Finance',
            '数字货币': 'Digital Currency', '区块链': 'Blockchain', '元宇宙': 'Metaverse',
            '云计算': 'Cloud Computing', '大数据': 'Big Data', '物联网': 'IoT',
            '5G': '5G', '工业互联网': 'Industrial Internet', '智能制造': 'Smart Manufacturing',
            '机器人': 'Robotics', '无人机': 'Drones', '3D打印': '3D Printing',
            '新材料': 'New Materials', '稀土': 'Rare Earth', '石墨烯': 'Graphene',
            '碳纤维': 'Carbon Fiber', '超导': 'Superconducting', '纳米材料': 'Nanomaterials',
            '核电': 'Nuclear Power', '风电': 'Wind Power', '水电': 'Hydropower',
            '储能': 'Energy Storage', '氢能源': 'Hydrogen Energy', '充电桩': 'Charging Piles',
            '特高压': 'UHV', '智能电网': 'Smart Grid', '电力物联网': 'Power IoT',
            '航运': 'Shipping', '港口': 'Ports', '航空': 'Aviation',
            '机场': 'Airports', '铁路': 'Railway', '公路': 'Highway',
            '公交': 'Public Transit', '网约车': 'Ride-hailing', '共享单车': 'Bike-sharing',
            '快递': 'Express Delivery', '外卖': 'Food Delivery', '电商': 'E-commerce',
            '跨境电商': 'Cross-border E-commerce', '直播带货': 'Live Commerce', '社区团购': 'Community Group Buying',
            '新零售': 'New Retail', '无人零售': 'Unmanned Retail', '便利店': 'Convenience Stores',
            '超市': 'Supermarkets', '百货': 'Department Stores', '购物中心': 'Shopping Malls',
            '专业连锁': 'Specialty Chain', '黄金珠宝': 'Gold & Jewelry', '化妆品': 'Cosmetics',
            '奢侈品': 'Luxury Goods', '钟表': 'Watches', '眼镜': 'Eyewear',
            '文具': 'Stationery', '玩具': 'Toys', '宠物': 'Pets',
            '园艺': 'Gardening', '家具': 'Furniture', '家居': 'Home Furnishings',
            '装修装饰': 'Decoration', '照明': 'Lighting', '厨卫': 'Kitchen & Bath',
            '家纺': 'Home Textiles', '塑料': 'Plastics', '橡胶': 'Rubber',
            '玻璃': 'Glass', '陶瓷': 'Ceramics', '造纸': 'Paper',
            '印刷': 'Printing', '包装': 'Packaging', '金属制品': 'Metal Products',
        }
        translated_industries = []
        for ind in news['industries']:
            if ind in industry_map:
                translated_industries.append(industry_map[ind])
            elif lang == 'en':
                # 对未映射的行业名称进行翻译
                translated_ind = translate_text(ind, 'en')
                translated_industries.append(translated_ind)
            else:
                translated_industries.append(ind)
        translated['industries'] = translated_industries
    
    # 翻译情感标签
    sentiment_map = {'正面': 'Positive', '负面': 'Negative', '中性': 'Neutral'}
    if news.get('sentiment'):
        translated['sentiment'] = sentiment_map.get(news['sentiment'], news['sentiment'])
    
    return translated

# ==================== 缓存功能 ====================
def get_cache_key(title):
    return hashlib.md5(title.encode('utf-8')).hexdigest()

def get_cached_analysis(title):
    key = get_cache_key(title)
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        cached_time = datetime.fromisoformat(cached['cached_at'])
        if datetime.now() - cached_time > timedelta(hours=CACHE_TTL_HOURS):
            os.remove(path)
            return None
        return cached['data']
    except:
        return None

def set_cached_analysis(title, data):
    key = get_cache_key(title)
    path = os.path.join(CACHE_DIR, f"{key}.json")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'cached_at': datetime.now().isoformat(), 'data': data}, f, ensure_ascii=False)
    except:
        pass

# ==================== 新闻抓取 ====================
class FinNews:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        if self.session:
            self.session.headers.update(self.headers)

    def _fetch(self, url, timeout=15):
        try:
            if REQUESTS_AVAILABLE and self.session:
                resp = self.session.get(url, timeout=timeout, verify=False)
                resp.encoding = resp.apparent_encoding or 'utf-8'
                return resp.text
            else:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    return r.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"  Fetch error: {url[:50]}... - {e}")
            return ""

    def get_eastmoney_news(self, limit=8):
        """东方财富 - 直接爬页面"""
        try:
            # 东方财富要闻页面
            url = 'https://finance.eastmoney.com/a/cywjh.html'
            html = self._fetch(url, timeout=20)
            if not html:
                return []
            
            news_list = []
            # 多种匹配模式
            patterns = [
                r'<a[^>]*href="(https?://finance\.eastmoney\.com/a/\d{8,}\.html)"[^>]*>\s*<[^>]*>([^<]{15,200})</',
                r'href="(https?://finance\.eastmoney\.com/a/\d{8,}\.html)"[^>]*>([^<]{15,200})</a>',
                r'<a[^>]*href="(https?://finance\.eastmoney\.com/a/\d{8,}\.html)"[^>]*title="([^"]{15,200})"',
            ]
            
            seen = set()
            for pattern in patterns:
                matches = re.findall(pattern, html)
                for link, title in matches:
                    title = title.strip()
                    if title and len(title) > 15 and title not in seen and not any(x in title for x in ['财经', '焦点', '股票', '行情', '数据', '新股']):
                        seen.add(title)
                        news_list.append({
                            'title': title,
                            'url': link if link.startswith('http') else f"https:{link}",
                            'source': '东方财富',
                            'time': datetime.now().isoformat()
                        })
                        if len(news_list) >= limit:
                            break
                if len(news_list) >= limit:
                    break
            
            print(f"  Eastmoney: {len(news_list)} items")
            return news_list
        except Exception as e:
            print(f"  Eastmoney error: {e}")
            return []

    def get_sina_finance(self, limit=5):
        """新浪财经 - 使用API"""
        try:
            # 新浪财经滚动新闻API
            url = f'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=30&r={int(time.time()*1000)}'
            html = self._fetch(url, timeout=15)
            
            news_list = []
            try:
                data = json.loads(html)
                items = data.get('result', {}).get('data', [])
                for item in items[:limit*2]:
                    title = item.get('title', '').strip()
                    url = item.get('url', '')
                    ctime = item.get('ctime', '')
                    if title and url and len(title) > 5:
                        news_list.append({
                            'title': title,
                            'url': url,
                            'source': '新浪财经',
                            'time': datetime.fromtimestamp(int(ctime)).isoformat() if ctime else datetime.now().isoformat()
                        })
                        if len(news_list) >= limit:
                            break
            except Exception as e:
                print(f"  Sina JSON error: {e}")
            
            print(f"  Sina: {len(news_list)} items")
            return news_list
        except Exception as e:
            print(f"  Sina error: {e}")
            return []

    def get_cls_news(self, limit=5):
        """财联社 - 爬取页面HTML"""
        try:
            url = 'https://www.cls.cn/telegraph'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            }
            
            if REQUESTS_AVAILABLE:
                resp = requests.get(url, headers=headers, timeout=15, verify=False)
                resp.encoding = 'utf-8'
                html = resp.text
            else:
                html = self._fetch(url)
            
            news_list = []
            
            # 移除script和style
            clean_html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
            clean_html = re.sub(r'<style[^>]*>.*?</style>', '', clean_html, flags=re.DOTALL)
            
            # 提取时间
            time_pattern = r'class="[^"]*telegraph-time-box[^"]*"[^>]*>(\d{2}:\d{2})</'
            times = re.findall(time_pattern, clean_html)
            
            # 提取内容 - 查找长度适中的文本
            text_pattern = r'>([^<]{40,300})<'
            texts = re.findall(text_pattern, clean_html)
            
            # 关键词过滤
            keywords = ['股', '市', '涨', '跌', '板', '元', '亿', '万', '公司', '发布', '公告', '业绩']
            seen = set()
            
            for i, text in enumerate(texts):
                text = text.strip()
                # 过滤条件
                if len(text) < 30 or len(text) > 200:
                    continue
                if text in seen:
                    continue
                # 必须包含财经关键词
                if not any(kw in text for kw in keywords):
                    continue
                # 排除常见垃圾内容
                if any(x in text for x in ['Copyright', '版权所有', '免责声明', '点击查看']):
                    continue
                    
                seen.add(text)
                time_str = times[i] if i < len(times) else ''
                
                news_list.append({
                    'title': text[:80] + '...' if len(text) > 80 else text,
                    'url': 'https://www.cls.cn/telegraph',
                    'source': '财联社',
                    'time': datetime.now().isoformat(),
                    'summary': text
                })
                
                if len(news_list) >= limit:
                    break
            
            print(f"  CLS: {len(news_list)} items (found {len(texts)} texts, {len(times)} times)")
            return news_list
        except Exception as e:
            print(f"  CLS error: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_all_news(self, limit=8):
        """获取所有新闻"""
        results = {}
        
        # 东方财富
        em = self.get_eastmoney_news(limit)
        if em:
            results['eastmoney'] = em
        
        # 新浪财经
        sina = self.get_sina_finance(limit)
        if sina:
            results['sina'] = sina
        
        # 财联社
        cls = self.get_cls_news(limit)
        if cls:
            results['cls'] = cls
        
        return results


class WeiboHotSearch:
    """微博热搜"""
    def __init__(self):
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        if self.session:
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://weibo.com/'
            })

    def get_hot_search(self, limit=20):
        """获取前20条微博热搜"""
        try:
            url = 'https://weibo.com/ajax/side/hotSearch'
            if not REQUESTS_AVAILABLE:
                return []
            resp = self.session.get(url, timeout=10, verify=False)
            data = resp.json()
            items = data.get('data', {}).get('realtime', [])
            results = []
            for i, item in enumerate(items[:limit]):
                title = item.get('word', '').strip()
                if title:
                    results.append({
                        'rank': i + 1,
                        'title': title,
                        'hot_score': item.get('raw_hot', 0),
                        'category': item.get('category', ''),
                        'source': '微博热搜'
                    })
            return results
        except Exception as e:
            print(f"  Weibo error: {e}")
            return []

    def search_weibo(self, keyword, limit=10):
        """搜索微博关键词"""
        try:
            # 使用微博搜索接口
            import urllib.parse
            encoded_keyword = urllib.parse.quote(keyword)
            url = f'https://m.weibo.cn/api/container/getIndex?containerid=100103type%3D1%26q%3D{encoded_keyword}&page_type=searchall'
            
            if not REQUESTS_AVAILABLE:
                return []
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15',
                'Referer': 'https://m.weibo.cn/'
            }
            
            resp = self.session.get(url, headers=headers, timeout=10, verify=False)
            data = resp.json()
            
            cards = data.get('data', {}).get('cards', [])
            results = []
            
            for card in cards[:limit]:
                if 'card_group' in card:
                    for item in card['card_group']:
                        mblog = item.get('mblog', {})
                        if mblog:
                            text = re.sub(r'<[^>]+>', '', mblog.get('text', ''))
                            if text and len(text) > 10:
                                results.append({
                                    'title': text[:100] + '...' if len(text) > 100 else text,
                                    'url': f"https://m.weibo.cn/detail/{mblog.get('mid', '')}",
                                    'source': '微博搜索',
                                    'time': datetime.now().isoformat(),
                                    'keyword': keyword
                                })
            
            print(f"  Weibo search '{keyword}': {len(results)} items")
            return results
        except Exception as e:
            print(f"  Weibo search error: {e}")
            return []


# ==================== AI分析 ====================



class NewsSearcher:
    """新闻搜索引擎"""
    def __init__(self):
        self.session = requests.Session() if REQUESTS_AVAILABLE else None
        if self.session:
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })

    def search_bing_news(self, keyword, limit=10):
        """使用 Bing News RSS 搜索新闻"""
        try:
            # Bing News RSS API
            url = f'https://www.bing.com/news/search?q={keyword}&format=rss'
            
            if not REQUESTS_AVAILABLE:
                return []
            
            resp = self.session.get(url, timeout=15, verify=False)
            resp.encoding = 'utf-8'
            
            # 解析 RSS
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            
            # RSS 命名空间
            ns = {'media': 'http://search.yahoo.com/mrss/'}
            
            results = []
            for item in root.findall('.//item')[:limit]:
                title = item.find('title')
                link = item.find('link')
                desc = item.find('description')
                pub_date = item.find('pubDate')
                
                if title is not None and link is not None:
                    title_text = re.sub(r'<[^>]+>', '', title.text or '')
                    results.append({
                        'title': title_text,
                        'url': link.text,
                        'source': 'Bing News',
                        'time': pub_date.text if pub_date else datetime.now().isoformat(),
                        'keyword': keyword
                    })
            
            print(f"  Bing News '{keyword}': {len(results)} items")
            return results
        except Exception as e:
            print(f"  Bing News error: {e}")
            return []

    def search_sogou_news(self, keyword, limit=10):
        """使用搜狗搜索新闻"""
        try:
            url = f'https://news.sogou.com/news?query={keyword}'
            
            if not REQUESTS_AVAILABLE:
                return []
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            
            resp = self.session.get(url, headers=headers, timeout=15, verify=False)
            resp.encoding = 'utf-8'
            html = resp.text
            
            results = []
            # 提取新闻标题和链接
            pattern = r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>.*?</h3>'
            matches = re.findall(pattern, html, re.DOTALL)
            
            for link, title in matches[:limit]:
                title_clean = re.sub(r'<[^>]+>', '', title).strip()
                if title_clean and len(title_clean) > 10:
                    results.append({
                        'title': title_clean,
                        'url': link if link.startswith('http') else f'https://news.sogou.com{link}',
                        'source': '搜狗新闻',
                        'time': datetime.now().isoformat(),
                        'keyword': keyword
                    })
            
            print(f"  Sogou News '{keyword}': {len(results)} items")
            return results
        except Exception as e:
            print(f"  Sogou News error: {e}")
            return []

    def search_by_keywords(self, keywords, limit=5):
        """根据关键词搜索多个来源的新闻"""
        all_news = []
        
        for keyword in keywords:
            print(f"[Search] Searching for: {keyword}")
            
            # 微博搜索
            weibo = WeiboHotSearch()
            weibo_results = weibo.search_weibo(keyword, limit)
            all_news.extend(weibo_results)
            
            # Bing 搜索
            bing_results = self.search_bing_news(keyword, limit)
            all_news.extend(bing_results)
            
            # 搜狗搜索
            sogou_results = self.search_sogou_news(keyword, limit)
            all_news.extend(sogou_results)
        
        # 去重
        seen = set()
        unique_news = []
        for news in all_news:
            key = news.get('title', '')[:50]
            if key and key not in seen:
                seen.add(key)
                unique_news.append(news)
        
        return unique_news[:limit * len(keywords)]


class HackerNews:
    """Hacker News"""
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

    def get_news(self, limit=10):
        try:
            url = 'https://news.ycombinator.com/'
            resp = self.session.get(url, timeout=15, verify=False)
            html = resp.text
            pattern = r'class="titleline"><a[^>]*>([^<]+)</a>'
            matches = re.findall(pattern, html)
            results = []
            seen = set()
            for title in matches:
                title = title.strip()
                if title and title not in seen and len(title) > 10:
                    seen.add(title)
                    results.append({'title': title, 'hot_score': len(matches) - len(seen), 'source': 'Hacker News', 'url': 'https://news.ycombinator.com/'})
                    if len(results) >= limit:
                        break
            return results
        except Exception as e:
            print(f"  Hacker News error: {e}")
            return []

def call_deepseek_api(prompt, max_tokens=400):
    if not DEEPSEEK_API_KEY:
        return None
    try:
        url = f"{DEEPSEEK_API_BASE}/v1/chat/completions"
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {DEEPSEEK_API_KEY}'}
        data = {'model': DEEPSEEK_MODEL, 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': max_tokens, 'temperature': 0.7}
        
        if REQUESTS_AVAILABLE:
            resp = requests.post(url, headers=headers, json=data, timeout=30, verify=False)
            return resp.json()['choices'][0]['message']['content'].strip()
        return None
    except Exception as e:
        print(f"  DeepSeek error: {e}")
        return None


def analyze_news(title, content=''):
    """分析新闻，带缓存"""
    cached = get_cached_analysis(title)
    if cached:
        return cached
    
    # 生成摘要
    summary_prompt = f"""为以下新闻生成摘要（不超过80字）：
标题：{title}
内容：{content[:500]}
直接输出摘要："""
    
    summary = call_deepseek_api(summary_prompt, 150)
    if not summary:
        # API 调用失败，返回默认值但不缓存
        return {
            'summary': '摘要生成中...',
            'industries': ['综合'],
            'sentiment': '中性',
            'impact': '分析中...',
            'trend': '建议关注',
            'stocks': []
        }
    
    if len(summary) > 80:
        summary = summary[:77] + "..."
    
    # 生成分析
    analysis_prompt = f"""分析以下新闻，返回JSON：
标题：{title}
摘要：{summary}
返回：{{"industries":["行业1"],"sentiment":"正面/负面/中性","impact":"影响","trend":"趋势","stocks":[]}}
只返回JSON："""
    
    result_text = call_deepseek_api(analysis_prompt, 300)
    
    try:
        analysis = json.loads(result_text) if result_text else {}
    except:
        match = re.search(r'\{.*\}', result_text or '', re.DOTALL)
        try:
            analysis = json.loads(match.group()) if match else {}
        except:
            analysis = {}
    
    result = {
        'summary': summary,
        'industries': analysis.get('industries', ['综合'])[:3],
        'sentiment': analysis.get('sentiment', '中性'),
        'impact': (analysis.get('impact', '分析中...') or '分析中...')[:40],
        'trend': (analysis.get('trend', '建议关注') or '建议关注')[:40],
        'stocks': analysis.get('stocks', [])[:5]
    }
    
    # 只有成功获取到摘要时才缓存
    if summary and summary != '摘要生成中...':
        set_cached_analysis(title, result)
    
    return result


def analyze_weibo_batch(hot_items):
    """
    批量分析微博热搜，筛选出对股市有影响的内容
    """
    if not hot_items:
        return []
    
    hot_list_text = "\n".join([f"{i+1}. {item['title']}" for i, item in enumerate(hot_items)])
    
    prompt = f"""你是一位专业的财经分析师。请分析以下微博热搜列表，找出对股市可能有影响的热搜。

微博热搜列表（按热度排名）：
{hot_list_text}

请分析每个热搜是否对股市有影响，考虑以下因素：
1. 是否涉及上市公司、行业政策、宏观经济
2. 是否可能引发市场情绪波动
3. 是否与技术趋势、消费热点相关（如AI、新能源、消费等）

请返回JSON数组格式，只包含有影响的热搜：
[
  {{
    "rank": 排名数字,
    "title": "热搜标题",
    "is_relevant": true,
    "reason": "对股市影响的具体原因（50字以内）",
    "industries": ["相关行业1", "行业2"],
    "sentiment": "正面/负面/中性",
    "stocks": ["可能受影响的股票代码1", "代码2"]
  }}
]

如果当前热搜都不影响股市，返回空数组 []。
只返回JSON，不要其他文字："""

    result_text = call_deepseek_api(prompt, 1000)
    
    if not result_text:
        print("  DeepSeek API调用失败")
        return []
    
    try:
        analysis_results = json.loads(result_text)
    except:
        match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if match:
            try:
                analysis_results = json.loads(match.group())
            except:
                return []
        else:
            return []
    
    if not analysis_results:
        print("  分析结果：当前热搜均不影响股市")
        return []
    
    relevant_items = []
    for analysis in analysis_results:
        if not analysis.get('is_relevant', False):
            continue
        
        rank = analysis.get('rank', 0)
        hot_item = None
        for item in hot_items:
            if item.get('rank') == rank:
                hot_item = item
                break
        
        if not hot_item:
            continue
        
        summary_prompt = f"""请为以下热搜生成一个简短的财经摘要（60字以内）：
热搜：{hot_item['title']}
影响原因：{analysis.get('reason', '')}
直接输出摘要："""
        
        summary = call_deepseek_api(summary_prompt, 100) or analysis.get('reason', '热门话题')
        if len(summary) > 60:
            summary = summary[:57] + "..."
        
        relevant_items.append({
            'title': hot_item['title'],
            'url': f"https://s.weibo.com/weibo?q={hot_item['title']}",
            'source': '微博热搜',
            'time': datetime.now().isoformat(),
            'hot_score': hot_item.get('hot_score', 0),
            'rank': rank,
            'ai_summary': summary,
            'sentiment': analysis.get('sentiment', '中性'),
            'impact': analysis.get('reason', '热门话题')[:50],
            'trend': f"热度排名 #{rank}",
            'industries': analysis.get('industries', ['综合'])[:3],
            'stocks': analysis.get('stocks', [])[:5],
            'is_social': True
        })
    
    print(f"  微博热搜分析完成：{len(hot_items)}条中筛选出{len(relevant_items)}条对股市有影响")
    return relevant_items


# ==================== 辅助函数 ====================
def format_time(time_str):
    try:
        dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        now = datetime.now()
        diff = now - dt.replace(tzinfo=None)
        if diff.days == 0:
            if diff.seconds < 3600:
                m = diff.seconds // 60
                return f'{m}分钟前' if m > 0 else '刚刚'
            return f'{diff.seconds // 3600}小时前'
        elif diff.days == 1:
            return '昨天'
        return dt.strftime('%m-%d')
    except:
        return time_str[:16] if time_str else ''


def get_source_class(source):
    mapping = {'东方财富': 'eastmoney', '新浪财经': 'sina', '财联社': 'cls', '微博热搜': 'weibo'}
    return mapping.get(source, 'default')


def get_sentiment_class(s):
    return {'正面': 'positive', '负面': 'negative'}.get(s, 'neutral')


# ==================== 新闻获取 ====================
def fetch_news_sync():
    """同步获取新闻"""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Fetching news...")
    
    client = FinNews()
    all_news = client.get_all_news(limit=6)
    
    print(f"  Sources available: {list(all_news.keys())}")
    
    combined = []
    
    # 优先从每个来源取新闻，确保多样性
    for source_key in ['eastmoney', 'sina', 'cls']:
        items = all_news.get(source_key, [])
        print(f"  Processing {source_key}: {len(items)} items")
        for item in items[:5]:  # 每个来源最多5条
            try:
                title = item.get('title', '')
                if not title or len(title) < 10:
                    continue
                
                # 检查缓存
                cached = get_cached_analysis(title)
                if cached:
                    ai_result = cached
                    print(f"    Cache hit: {title[:40]}...")
                else:
                    print(f"    Analyzing: {title[:40]}...")
                    ai_result = analyze_news(title, item.get('summary', ''))
                
                combined.append({
                    'title': title,
                    'url': item.get('url', '#'),
                    'source': item.get('source', source_key),
                    'time': item.get('time', datetime.now().isoformat()),
                    'ai_summary': ai_result['summary'],
                    'sentiment': ai_result['sentiment'],
                    'impact': ai_result['impact'],
                    'trend': ai_result['trend'],
                    'industries': ai_result['industries'],
                    'stocks': ai_result['stocks'],
                    'source_class': get_source_class(item.get('source', source_key)),
                    'time_display': format_time(item.get('time', '')),
                    'sentiment_class': get_sentiment_class(ai_result['sentiment']),
                    'is_social': False
                })
            except Exception as e:
                print(f"    Error: {e}")
    
    # 处理微博热搜 - 新逻辑：AI批量分析
    try:
        print("  Fetching Weibo hot search...")
        weibo = WeiboHotSearch()
        hot_items = weibo.get_hot_search(limit=20)  # 获取前20条
        print(f"  Weibo hot search: got {len(hot_items)} items")
        
        if hot_items:
            # 批量分析，筛选对股市有影响的热搜
            weibo_results = analyze_weibo_batch(hot_items)
            
            for result in weibo_results:
                combined.append({
                    'title': result['title'],
                    'url': result['url'],
                    'source': '微博热搜',
                    'time': result['time'],
                    'ai_summary': result['ai_summary'],
                    'sentiment': result['sentiment'],
                    'impact': result['impact'],
                    'trend': result['trend'],
                    'industries': result['industries'],
                    'stocks': result['stocks'],
                    'source_class': 'weibo',
                    'time_display': f"热度 {result['hot_score']}",
                    'sentiment_class': get_sentiment_class(result['sentiment']),
                    'is_social': True,
                    'rank': result.get('rank')
                })
                print(f"    Added weibo: #{result.get('rank')} {result['title'][:40]}...")
    except Exception as e:
        print(f"  Weibo error: {e}")
        import traceback
        traceback.print_exc()
    
    # 排序：财经新闻按时间，微博按排名
    combined.sort(key=lambda x: (0 if not x['is_social'] else 1, x.get('rank', 99) if x['is_social'] else 0), reverse=False)
    
    print(f"  Total combined: {len(combined)} items")
    return combined[:30]  # 最多15条


def get_cached_news():
    """获取缓存的新闻"""
    global _global_news_cache
    
    with _global_news_cache['lock']:
        if _global_news_cache['data'] and _global_news_cache['timestamp']:
            if datetime.now() - _global_news_cache['timestamp'] < timedelta(hours=12):
                return _global_news_cache['data']
        
        data = fetch_news_sync()
        _global_news_cache['data'] = data
        _global_news_cache['timestamp'] = datetime.now()
        return data


# ==================== HTML模板 ====================


# ==================== API 接口 ====================
@app.route('/api/v1/news', methods=['GET', 'POST'])
def api_v1_news():
    """
    API接口：根据行业和语言获取新闻
    
    Headers:
        API_SECRET: 认证密钥
    
    Query Params:
        lang: 语言 (zh/en)，默认 zh
    
    Body (JSON):
        field: 数组，表示查询的行业，如 ["科技", "AI"]
        ticker: 数组，股票代码列表，如 ["600519", "000001"]
        company: 数组，公司名称列表，如 ["茅台", "宁德时代"]
    
    Returns:
        HTML格式的卡片页面
    """
    # 1. 认证检查 - 支持多种请求头格式
    api_secret = (
        request.headers.get('API_SECRET', '') or 
        request.headers.get('Api-Secret', '') or
        request.headers.get('api-secret', '') or
        request.args.get('api_secret', '')
    )
    if api_secret != API_SECRET:
        return jsonify({'error': 'Unauthorized', 'message': 'Invalid or missing API_SECRET'}), 401
    
    # 2. 参数解析
    lang = request.args.get('lang', 'zh').lower()
    if lang not in ['zh', 'en']:
        lang = 'zh'
    
    # 从 body 解析参数
    fields = []
    tickers = []
    companies = []
    
    if request.is_json:
        body = request.get_json() or {}
        
        # 解析 field（行业）
        if 'field' in body:
            field_data = body['field']
            if isinstance(field_data, list):
                fields = field_data
            elif isinstance(field_data, str):
                try:
                    fields = json.loads(field_data)
                except:
                    fields = []
        
        # 解析 ticker（股票代码）
        if 'ticker' in body:
            ticker_data = body['ticker']
            if isinstance(ticker_data, list):
                tickers = ticker_data
            elif isinstance(ticker_data, str):
                try:
                    tickers = json.loads(ticker_data)
                except:
                    tickers = []
        
        # 解析 company（公司名称）
        if 'company' in body:
            company_data = body['company']
            if isinstance(company_data, list):
                companies = company_data
            elif isinstance(company_data, str):
                try:
                    companies = json.loads(company_data)
                except:
                    companies = []
    
    # 3. 获取新闻数据
    news_list = get_cached_news()
    
    # 4. 根据 fields/tickers/companies 过滤新闻
    search_keywords = []
    if fields:
        search_keywords.extend(fields)
    if tickers:
        search_keywords.extend(tickers)
    if companies:
        search_keywords.extend(companies)
    
    if search_keywords:
        filtered_news = []
        for news in news_list:
            match = False
            news_title = news.get('title', '').lower()
            news_industries = news.get('industries', [])
            news_stocks = news.get('stocks', [])
            
            # 检查 field（行业）匹配
            if fields:
                for field in fields:
                    if field in news_industries or field.lower() in news_title:
                        match = True
                        break
            
            # 检查 ticker（股票代码）匹配
            if not match and tickers:
                for ticker in tickers:
                    if ticker in news_stocks or ticker.lower() in news_title:
                        match = True
                        break
            
            # 检查 company（公司名称）匹配
            if not match and companies:
                for company in companies:
                    if company.lower() in news_title:
                        match = True
                        break
            
            if match:
                filtered_news.append(news)
        
        # 如果缓存中没有找到足够的新闻，使用搜索引擎补充
        search_terms = []
        if companies:
            search_terms.extend(companies)
        if tickers:
            # 将股票代码转换为搜索词（如 600519 -> 茅台）
            for ticker in tickers:
                search_terms.append(ticker)
        
        if len(filtered_news) < 3 and search_terms:
            print(f"[API] Cache miss for {search_terms}, searching online...")
            searcher = NewsSearcher()
            online_news = searcher.search_by_keywords(search_terms, limit=5)
            
            # 对搜索到的新闻进行 AI 分析
            for news in online_news:
                analysis = analyze_news(news['title'])
                filtered_news.append({
                    'title': news['title'],
                    'url': news['url'],
                    'source': news['source'],
                    'time': news['time'],
                    'ai_summary': analysis['summary'],
                    'sentiment': analysis['sentiment'],
                    'impact': analysis['impact'],
                    'trend': analysis['trend'],
                    'industries': analysis['industries'],
                    'stocks': analysis['stocks'],
                    'source_class': 'default',
                    'time_display': '刚刚',
                    'sentiment_class': get_sentiment_class(analysis['sentiment']),
                    'is_social': False
                })
        
        news_list = filtered_news
    
    # 5. 如果需要英文，翻译新闻
    if lang == 'en':
        news_list = [translate_news_item(n, 'en') for n in news_list]
    
    # 6. 分离财经新闻和微博热搜
    finance_news = [n for n in news_list if not n.get('is_social')]
    weibo_news = [n for n in news_list if n.get('is_social')]
    
    # 7. 生成 HTML 响应
    html_template = API_HTML_TEMPLATE
    
    # 渲染模板
    html = render_template_string(
        html_template,
        lang=lang,
        title=get_text('title', lang),
        subtitle=get_text('subtitle', lang),
        finance_news=finance_news,
        weibo_news=weibo_news,
        news_count=len(news_list),
        count_suffix=get_text('count_suffix', lang),
        update_time=get_text('update_time', lang),
        update_time_value=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        finance_news_title=get_text('finance_news', lang),
        weibo_hot_title=get_text('weibo_hot', lang),
        weibo_empty=get_text('weibo_empty', lang),
        weibo_analyzed=get_text('weibo_analyzed', lang),
        summary=get_text('summary', lang),
        weibo_summary=get_text('weibo_summary', lang),
        ai_analysis=get_text('ai_analysis', lang),
        market_impact=get_text('market_impact', lang),
        investment_direction=get_text('investment_direction', lang),
        rank=get_text('rank', lang),
        rank_suffix=get_text('rank_suffix', lang),
        read_more=get_text('read_more', lang),
        view_hot=get_text('view_hot', lang),
        loading=get_text('loading', lang),
        hot_search=get_text('hot_search', lang),
        query_fields=', '.join(fields) if fields else 'All'
    )
    
    # 返回 HTML
    response = make_response(html)
    response.headers['Content-Type'] = 'text/html; charset=utf-8'
    return response


API_HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }} - {{ subtitle }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; color: white; position: relative; }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; text-shadow: 2px 2px 4px rgba(0,0,0,0.2); }
        .header .subtitle { font-size: 1.1em; opacity: 0.9; }
        .header .time { opacity: 0.8; font-size: 0.9em; margin-top: 5px; }
        .query-info { background: rgba(255,255,255,0.2); border-radius: 12px; padding: 12px 20px; margin-top: 15px; display: inline-block; }
        .query-info span { font-size: 0.9em; opacity: 0.9; }
        .section-title { color: white; font-size: 1.3em; margin: 30px 0 15px 0; padding-left: 10px; border-left: 4px solid #fff; }
        .news-grid { display: grid; gap: 20px; }
        .news-card { background: white; border-radius: 16px; padding: 24px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); transition: transform 0.3s, box-shadow 0.3s; cursor: pointer; text-decoration: none; color: inherit; display: block; }
        .news-card:hover { transform: translateY(-5px); box-shadow: 0 15px 50px rgba(0,0,0,0.2); }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
        .source-tag { display: inline-flex; align-items: center; padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; }
        .source-eastmoney { background: #e3f2fd; color: #1976d2; }
        .source-sina { background: #fce4ec; color: #c2185b; }
        .source-cls { background: #e8f5e9; color: #388e3c; }
        .source-weibo { background: #fff3e0; color: #e65100; }
        .rank-badge { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; font-size: 0.75em; font-weight: 700; margin-right: 8px; }
        .rank-top3 { background: linear-gradient(135deg, #ff6b6b, #ee5a5a); color: white; }
        .rank-other { background: #e0e0e0; color: #616161; }
        .weibo-section { margin-top: 20px; }
        .weibo-empty { text-align: center; color: white; padding: 30px; background: rgba(255,255,255,0.1); border-radius: 12px; margin: 20px 0; }
        .news-time { font-size: 0.8em; color: #9e9e9e; }
        .news-title { font-size: 1.2em; font-weight: 600; line-height: 1.5; color: #212121; margin-bottom: 16px; }
        .ai-summary { background: #f8f9fa; border-radius: 12px; padding: 16px; margin-bottom: 16px; border-left: 4px solid #28a745; }
        .ai-summary-header { display: flex; align-items: center; margin-bottom: 8px; font-size: 0.85em; font-weight: 600; color: #28a745; }
        .ai-summary-content { font-size: 0.95em; color: #424242; line-height: 1.6; }
        .ai-analysis { background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%); border-radius: 12px; padding: 16px; border-left: 4px solid #667eea; }
        .ai-header { display: flex; align-items: center; margin-bottom: 12px; font-size: 0.85em; font-weight: 600; color: #667eea; }
        .sentiment-tag { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.75em; margin-left: 10px; font-weight: 500; }
        .sentiment-positive { background: #e8f5e9; color: #2e7d32; }
        .sentiment-negative { background: #ffebee; color: #c62828; }
        .sentiment-neutral { background: #f5f5f5; color: #616161; }
        .analysis-content { font-size: 0.9em; color: #424242; line-height: 1.6; }
        .analysis-item { margin: 8px 0; padding-left: 16px; position: relative; }
        .analysis-item::before { content: "•"; position: absolute; left: 0; color: #667eea; font-weight: bold; }
        .analysis-label { font-weight: 600; color: #667eea; }
        .industry-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
        .industry-tag { background: #667eea; color: white; padding: 4px 12px; border-radius: 15px; font-size: 0.8em; font-weight: 500; }
        .stock-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .stock-tag { background: #ff9800; color: white; padding: 3px 10px; border-radius: 15px; font-size: 0.75em; font-weight: 500; }
        .card-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding-top: 16px; border-top: 1px solid #f0f0f0; }
        .read-more { color: #667eea; font-size: 0.9em; font-weight: 500; }
        .social-badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 10px; font-size: 0.7em; margin-left: 8px; font-weight: 500; background: #ff5722; color: white; }
        @media (max-width: 600px) { body { padding: 10px; } .header h1 { font-size: 1.8em; } .news-card { padding: 18px; } .news-title { font-size: 1.05em; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ title }}</h1>
            <div class="subtitle">{{ subtitle }}</div>
            <div class="time">{{ update_time }}: {{ update_time_value }} | {{ news_count }} {{ count_suffix }}</div>
            <div class="query-info"><span>Query: {{ query_fields }}</span></div>
        </div>
        
        {% if finance_news %}
        <h2 class="section-title">{{ finance_news_title }}</h2>
        <div class="news-grid">
            {% for news in finance_news %}
            <a href="{{ news.url }}" target="_blank" class="news-card">
                <div class="card-header">
                    <span class="source-tag source-{{ news.source_class }}">{{ news.source }}</span>
                    <span class="news-time">{{ news.time_display }}</span>
                </div>
                <div class="news-title">{{ news.title }}</div>
                <div class="ai-summary">
                    <div class="ai-summary-header">{{ summary }}</div>
                    <div class="ai-summary-content">{{ news.ai_summary }}</div>
                </div>
                <div class="ai-analysis">
                    <div class="ai-header">{{ ai_analysis }}<span class="sentiment-tag sentiment-{{ news.sentiment_class }}">{{ news.sentiment }}</span></div>
                    <div class="analysis-content">
                        <div class="analysis-item"><span class="analysis-label">{{ market_impact }}:</span> {{ news.impact }}</div>
                        <div class="analysis-item"><span class="analysis-label">{{ investment_direction }}:</span> {{ news.trend }}</div>
                    </div>
                    {% if news.industries %}<div class="industry-tags">{% for ind in news.industries %}<span class="industry-tag">{{ ind }}</span>{% endfor %}</div>{% endif %}
                    {% if news.stocks %}<div class="stock-tags">{% for stock in news.stocks %}<span class="stock-tag">{{ stock }}</span>{% endfor %}</div>{% endif %}
                </div>
                <div class="card-footer"><span></span><span class="read-more">{{ read_more }} →</span></div>
            </a>
            {% endfor %}
        </div>
        {% endif %}
        
        {% if weibo_news %}
        <div class="weibo-section">
            <h2 class="section-title">{{ weibo_hot_title }}</h2>
            <div class="news-grid">
                {% for news in weibo_news %}
                <a href="{{ news.url }}" target="_blank" class="news-card">
                    <div class="card-header">
                        <span class="source-tag source-weibo">
                            <span class="rank-badge {{ 'rank-top3' if news.rank <= 3 else 'rank-other' }}">{{ news.rank }}</span>
                            {{ news.source }}
                        </span>
                        <span class="news-time">{{ news.time_display }}</span>
                        <span class="social-badge">{{ hot_search }}</span>
                    </div>
                    <div class="news-title">{{ news.title }}</div>
                    <div class="ai-summary">
                        <div class="ai-summary-header">{{ weibo_summary }}</div>
                        <div class="ai-summary-content">{{ news.ai_summary }}</div>
                    </div>
                    <div class="ai-analysis">
                        <div class="ai-header">{{ ai_analysis }}<span class="sentiment-tag sentiment-{{ news.sentiment_class }}">{{ news.sentiment }}</span></div>
                        <div class="analysis-content">
                            <div class="analysis-item"><span class="analysis-label">{{ market_impact }}:</span> {{ news.impact }}</div>
                            <div class="analysis-item"><span class="analysis-label">{{ rank }}:</span> {{ news.rank }} {{ rank_suffix }}</div>
                        </div>
                        {% if news.industries %}<div class="industry-tags">{% for ind in news.industries %}<span class="industry-tag">{{ ind }}</span>{% endfor %}</div>{% endif %}
                        {% if news.stocks %}<div class="stock-tags">{% for stock in news.stocks %}<span class="stock-tag">{{ stock }}</span>{% endfor %}</div>{% endif %}
                    </div>
                    <div class="card-footer"><span></span><span class="read-more">{{ view_hot }} →</span></div>
                </a>
                {% endfor %}
            </div>
        </div>
        {% else %}
        <div class="weibo-section">
            <h2 class="section-title">{{ weibo_hot_title }}</h2>
            <div class="weibo-empty">
                <p>{{ weibo_empty }}</p>
                <p style="font-size: 0.9em; margin-top: 10px; opacity: 0.8;">{{ weibo_analyzed }}</p>
            </div>
        </div>
        {% endif %}
        
        {% if not finance_news and not weibo_news %}
        <div style="text-align:center;color:white;padding:40px;"><h2>{{ loading }}</h2></div>
        {% endif %}
    </div>
</body>
</html>'''

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>财经新闻 - AI智能分析</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; color: white; }
        .header h1 { font-size: 2.5em; margin-bottom: 10px; text-shadow: 2px 2px 4px rgba(0,0,0,0.2); }
        .header .subtitle { font-size: 1.1em; opacity: 0.9; }
        .header .time { opacity: 0.8; font-size: 0.9em; margin-top: 5px; }
        .section-title { color: white; font-size: 1.3em; margin: 30px 0 15px 0; padding-left: 10px; border-left: 4px solid #fff; }
        .news-grid { display: grid; gap: 20px; }
        .news-card { background: white; border-radius: 16px; padding: 24px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); transition: transform 0.3s, box-shadow 0.3s; cursor: pointer; text-decoration: none; color: inherit; display: block; }
        .news-card:hover { transform: translateY(-5px); box-shadow: 0 15px 50px rgba(0,0,0,0.2); }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
        .source-tag { display: inline-flex; align-items: center; padding: 4px 12px; border-radius: 20px; font-size: 0.75em; font-weight: 600; }
        .source-eastmoney { background: #e3f2fd; color: #1976d2; }
        .source-sina { background: #fce4ec; color: #c2185b; }
        .source-cls { background: #e8f5e9; color: #388e3c; }
        .source-weibo { background: #fff3e0; color: #e65100; }
.source-hackernews { background: #ff6600; color: white; }
        .rank-badge { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; font-size: 0.75em; font-weight: 700; margin-right: 8px; }
        .rank-top3 { background: linear-gradient(135deg, #ff6b6b, #ee5a5a); color: white; }
        .rank-other { background: #e0e0e0; color: #616161; }
        .weibo-section { margin-top: 20px; }
        .weibo-empty { text-align: center; color: white; padding: 30px; background: rgba(255,255,255,0.1); border-radius: 12px; margin: 20px 0; }
        .source-default { background: #f5f5f5; color: #616161; }
        .news-time { font-size: 0.8em; color: #9e9e9e; }
        .news-title { font-size: 1.2em; font-weight: 600; line-height: 1.5; color: #212121; margin-bottom: 16px; }
        .ai-summary { background: #f8f9fa; border-radius: 12px; padding: 16px; margin-bottom: 16px; border-left: 4px solid #28a745; }
        .ai-summary-header { display: flex; align-items: center; margin-bottom: 8px; font-size: 0.85em; font-weight: 600; color: #28a745; }
        .ai-summary-content { font-size: 0.95em; color: #424242; line-height: 1.6; }
        .ai-analysis { background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%); border-radius: 12px; padding: 16px; border-left: 4px solid #667eea; }
        .ai-header { display: flex; align-items: center; margin-bottom: 12px; font-size: 0.85em; font-weight: 600; color: #667eea; }
        .sentiment-tag { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.75em; margin-left: 10px; font-weight: 500; }
        .sentiment-positive { background: #e8f5e9; color: #2e7d32; }
        .sentiment-negative { background: #ffebee; color: #c62828; }
        .sentiment-neutral { background: #f5f5f5; color: #616161; }
        .analysis-content { font-size: 0.9em; color: #424242; line-height: 1.6; }
        .analysis-item { margin: 8px 0; padding-left: 16px; position: relative; }
        .analysis-item::before { content: "•"; position: absolute; left: 0; color: #667eea; font-weight: bold; }
        .analysis-label { font-weight: 600; color: #667eea; }
        .industry-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
        .industry-tag { background: #667eea; color: white; padding: 4px 12px; border-radius: 15px; font-size: 0.8em; font-weight: 500; }
        .stock-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .stock-tag { background: #ff9800; color: white; padding: 3px 10px; border-radius: 15px; font-size: 0.75em; font-weight: 500; }
        .card-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding-top: 16px; border-top: 1px solid #f0f0f0; }
        .read-more { color: #667eea; font-size: 0.9em; font-weight: 500; }
        .social-badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 10px; font-size: 0.7em; margin-left: 8px; font-weight: 500; background: #ff5722; color: white; }
        @media (max-width: 600px) { body { padding: 10px; } .header h1 { font-size: 1.8em; } .news-card { padding: 18px; } .news-title { font-size: 1.05em; } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📰 财经新闻</h1>
            <div class="subtitle">{{ subtitle }}</div>
            <div class="time">更新时间: {{ update_time }} | 共 {{ news_count }} 条</div>
        </div>
        
        {% if finance_news %}
        <h2 class="section-title">📈 财经要闻</h2>
        <div class="news-grid">
            {% for news in finance_news %}
            <a href="{{ news.url }}" target="_blank" class="news-card">
                <div class="card-header">
                    <span class="source-tag source-{{ news.source_class }}">{{ news.source }}</span>
                    <span class="news-time">{{ news.time_display }}</span>
                </div>
                <div class="news-title">{{ news.title }}</div>
                <div class="ai-summary">
                    <div class="ai-summary-header">📝 {{ summary }}</div>
                    <div class="ai-summary-content">{{ news.ai_summary }}</div>
                </div>
                <div class="ai-analysis">
                    <div class="ai-header">🤖 {{ ai_analysis }}<span class="sentiment-tag sentiment-{{ news.sentiment_class }}">{{ news.sentiment }}</span></div>
                    <div class="analysis-content">
                        <div class="analysis-item"><span class="analysis-label">{{ market_impact }}:</span>{{ news.impact }}</div>
                        <div class="analysis-item"><span class="analysis-label">{{ investment_direction }}:</span>{{ news.trend }}</div>
                    </div>
                    {% if news.industries %}<div class="industry-tags">{% for ind in news.industries %}<span class="industry-tag">{{ ind }}</span>{% endfor %}</div>{% endif %}
                    {% if news.stocks %}<div class="stock-tags">{% for stock in news.stocks %}<span class="stock-tag">{{ stock }}</span>{% endfor %}</div>{% endif %}
                </div>
                <div class="card-footer"><span></span><span class="read-more">{{ read_more }} →</span></div>
            </a>
            {% endfor %}
        </div>
        {% endif %}
        
        {% if weibo_news %}
        <div class="weibo-section">
            <h2 class="section-title">🔥 微博热搜 · 股市相关</h2>
            <div class="news-grid">
                {% for news in weibo_news %}
                <a href="{{ news.url }}" target="_blank" class="news-card">
                    <div class="card-header">
                        <span class="source-tag source-weibo">
                            <span class="rank-badge {{ 'rank-top3' if news.rank <= 3 else 'rank-other' }}">{{ news.rank }}</span>
                            微博热搜
                        </span>
                        <span class="news-time">{{ news.time_display }}</span>
                        <span class="social-badge">{{ hot_search }}</span>
                    </div>
                    <div class="news-title">{{ news.title }}</div>
                    <div class="ai-summary">
                        <div class="ai-summary-header">📝 {{ weibo_summary }}</div>
                        <div class="ai-summary-content">{{ news.ai_summary }}</div>
                    </div>
                    <div class="ai-analysis">
                        <div class="ai-header">🤖 {{ ai_analysis }}<span class="sentiment-tag sentiment-{{ news.sentiment_class }}">{{ news.sentiment }}</span></div>
                        <div class="analysis-content">
                            <div class="analysis-item"><span class="analysis-label">{{ market_impact }}:</span>{{ news.impact }}</div>
                            <div class="analysis-item"><span class="analysis-label">{{ rank }}:</span>第 {{ news.rank }} 名</div>
                        </div>
                        {% if news.industries %}<div class="industry-tags">{% for ind in news.industries %}<span class="industry-tag">{{ ind }}</span>{% endfor %}</div>{% endif %}
                        {% if news.stocks %}<div class="stock-tags">{% for stock in news.stocks %}<span class="stock-tag">{{ stock }}</span>{% endfor %}</div>{% endif %}
                    </div>
                    <div class="card-footer"><span></span><span class="read-more">{{ view_hot }} →</span></div>
                </a>
                {% endfor %}
            </div>
        </div>
        {% else %}
        <div class="weibo-section">
            <h2 class="section-title">🔥 微博热搜 · 股市相关</h2>
            <div class="weibo-empty">
                <p>{{ weibo_empty }}</p>
                <p style="font-size: 0.9em; margin-top: 10px; opacity: 0.8;">{{ weibo_analyzed }}</p>
            </div>
        </div>
        {% endif %}
        
        {% if not finance_news and not weibo_news %}
        <div style="text-align:center;color:white;padding:40px;"><h2>{{ loading }}</h2></div>
        {% endif %}
    </div>
</body>
</html>'''


# ==================== Flask路由 ====================
@app.route('/')
def index():
    # 获取语言参数
    lang = request.args.get('lang', 'zh')
    if lang not in ['zh', 'en']:
        lang = 'zh'
    other_lang = 'en' if lang == 'zh' else 'zh'
    
    # 获取新闻数据
    news_list = get_cached_news()
    
    # 如果需要英文，翻译新闻内容
    if lang == 'en':
        news_list = [translate_news_item(n, 'en') for n in news_list]
    
    finance_news = [n for n in news_list if not n.get('is_social')]
    weibo_news = [n for n in news_list if n.get('is_social')]
    
    return render_template_string(HTML_TEMPLATE,
                                  lang=lang,
                                  other_lang=other_lang,
                                  title=get_text('title', lang),
                                  subtitle=get_text('subtitle', lang),
                                  finance_news=finance_news,
                                  weibo_news=weibo_news,
                                  news_count=len(news_list),
                                  count_suffix=get_text('count_suffix', lang),
                                  update_time=get_text('update_time', lang),
                                  update_time_value=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                  switch_lang=get_text('switch_lang', lang),
                                  finance_news_title=get_text('finance_news', lang),
                                  weibo_hot_title=get_text('weibo_hot', lang),
                                  weibo_empty=get_text('weibo_empty', lang),
                                  weibo_analyzed=get_text('weibo_analyzed', lang),
                                  summary=get_text('summary', lang),
                                  weibo_summary=get_text('weibo_summary', lang),
                                  ai_analysis=get_text('ai_analysis', lang),
                                  market_impact=get_text('market_impact', lang),
                                  investment_direction=get_text('investment_direction', lang),
                                  rank=get_text('rank', lang),
                                  rank_suffix=get_text('rank_suffix', lang),
                                  read_more=get_text('read_more', lang),
                                  view_hot=get_text('view_hot', lang),
                                  loading=get_text('loading', lang),
                                  hot_search=get_text('hot_search', lang))


@app.route('/api/news')
def api_news():
    return jsonify({'success': True, 'count': len(get_cached_news()), 'data': get_cached_news()})


@app.route('/api/cache/stats')
def cache_stats():
    try:
        files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.json')]
        total_size = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
        return jsonify({'cache_count': len(files), 'cache_size_mb': round(total_size / 1024 / 1024, 2)})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/health')
def health():
    global _last_auto_refresh
    return jsonify({
        'status': 'ok', 
        'deepseek_configured': bool(DEEPSEEK_API_KEY),
        'auto_refresh_interval_hours': AUTO_REFRESH_INTERVAL_HOURS,
        'last_auto_refresh': _last_auto_refresh.isoformat() if _last_auto_refresh else None
    })


def auto_refresh_worker():
    """后台定时刷新线程"""
    global _global_news_cache, _last_auto_refresh, AUTO_REFRESH_INTERVAL_HOURS
    
    print(f"[AutoRefresh] Worker started, interval: {AUTO_REFRESH_INTERVAL_HOURS} hours")
    
    while True:
        try:
            now = datetime.now()
            
            # 检查是否需要刷新
            need_refresh = False
            with _auto_refresh_lock:
                if _last_auto_refresh is None:
                    need_refresh = True
                else:
                    time_since_last = now - _last_auto_refresh
                    if time_since_last >= timedelta(hours=AUTO_REFRESH_INTERVAL_HOURS):
                        need_refresh = True
            
            if need_refresh:
                print(f"\n[AutoRefresh] Starting auto refresh at {now}")
                try:
                    # 执行新闻抓取
                    data = fetch_news_sync()
                    
                    # 更新缓存
                    with _global_news_cache['lock']:
                        _global_news_cache['data'] = data
                        _global_news_cache['timestamp'] = now
                    
                    with _auto_refresh_lock:
                        _last_auto_refresh = now
                    
                    print(f"[AutoRefresh] Completed at {datetime.now()}, got {len(data)} items")
                except Exception as e:
                    print(f"[AutoRefresh] Error during refresh: {e}")
                    import traceback
                    traceback.print_exc()
            
            # 每分钟检查一次
            time.sleep(60)
            
        except Exception as e:
            print(f"[AutoRefresh] Worker error: {e}")
            time.sleep(60)


def start_auto_refresh():
    """启动定时刷新线程"""
    thread = threading.Thread(target=auto_refresh_worker, daemon=True)
    thread.start()
    print(f"[AutoRefresh] Thread started")


if __name__ == '__main__':
    print("Starting server...")
    
    # 首次加载数据
    print("[Init] Loading initial data...")
    initial_data = fetch_news_sync()
    with _global_news_cache['lock']:
        _global_news_cache['data'] = initial_data
        _global_news_cache['timestamp'] = datetime.now()
    
    _last_auto_refresh = datetime.now()
    print(f"[Init] Loaded {len(initial_data)} items")
    
    # 启动定时刷新线程
    start_auto_refresh()
    
    # 启动Flask服务
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False, threaded=True)
