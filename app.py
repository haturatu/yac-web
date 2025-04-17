from fastapi import FastAPI, Request, Query, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.gzip import GZipMiddleware
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import quote_plus
from typing import Optional
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# キャッシュコントロールミドルウェアの作成
class CacheControlMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, cache_timeout: int = 3600):
        super().__init__(app)
        self.cache_timeout = cache_timeout

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # 静的ファイルに対してキャッシュヘッダーを設定
        if request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = f"public, max-age={self.cache_timeout}"
            response.headers["Expires"] = time.strftime(
                "%a, %d %b %Y %H:%M:%S GMT", 
                time.gmtime(time.time() + self.cache_timeout)
            )
        
        return response

# 画像圧縮ミドルウェア
class ImageOptimizationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # 画像リクエストの場合、Content-Encodingヘッダーを追加
        if request.url.path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            response.headers["Vary"] = "Accept-Encoding"
        
        return response

app = FastAPI()

# GZip圧縮を有効化（レベル9は最大圧縮率）
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=9)
# キャッシュコントロールミドルウェアを追加
app.add_middleware(CacheControlMiddleware, cache_timeout=86400)  # 24時間キャッシュ
# 画像最適化ミドルウェアを追加
app.add_middleware(ImageOptimizationMiddleware)

# 静的ファイルの設定
app.mount("/static", StaticFiles(directory="static"), name="static")

# テンプレートの設定
templates = Jinja2Templates(directory="templates")

# ユーザーエージェントを設定
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# レスポンス圧縮用ヘルパー関数
def create_compressed_response(template_name: str, context: dict, headers: dict = None):
    """テンプレートレスポンスを生成し、圧縮関連ヘッダーを追加する"""
    response = templates.TemplateResponse(template_name, context)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Vary"] = "Accept-Encoding"
    
    if headers:
        for key, value in headers.items():
            response.headers[key] = value
    
    return response

async def scrape_yahoo_auctions(
    search_query: Optional[str] = None,
    category: Optional[str] = None,
    page: int = 1,
    closed: bool = False,
    sort_by: Optional[str] = None,
    sort_order: str = 'asc',
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    auchours: Optional[int] = None
):
    """ヤフオクから商品情報をスクレイピングする関数"""
    if not search_query and not category:
        return {
            'items': [],
            'categories': [],
            'current_page': page,
            'search_query': search_query,
            'category': category,
            'closed': closed,
            'sort_by': sort_by,
            'sort_order': sort_order,
            'price_min': price_min,
            'price_max': price_max,
            'auchours': auchours,
            'error': '検索クエリまたはカテゴリが指定されていません'
        }
    
    # 検索クエリがある場合はURLを構築
    if search_query:
        # スペースを + に置換してからエンコード
        encoded_query = quote_plus(search_query.replace(" ", "+"))
        base_url = "https://auctions.yahoo.co.jp/closedsearch/closedsearch" if closed else "https://auctions.yahoo.co.jp/search/search"
        url = f"{base_url}?p={encoded_query}&va={encoded_query}"
        
        # 価格範囲
        if price_min is not None:
            url += f"&aucminprice={price_min}&min={price_min}"
        if price_max is not None:
            url += f"&aucmaxprice={price_max}&max={price_max}"
        
        # 時間以内に終了する絞り込み
        if not closed and auchours is not None:
            url += f"&auchours={auchours}"
        
        # ソート順
        if sort_by == 'price':
            url += "&s1=cbids" if sort_order == 'asc' else "&s1=cbids&o1=d"
        elif sort_by == 'bids':
            url += "&s1=bids" if sort_order == 'asc' else "&s1=bids&o1=d"
        elif sort_by == 'time':
            url += "&s1=end" if sort_order == 'asc' else "&s1=end&o1=d"
        
        # ページ番号
        url += f"&b={(page-1)*50+1}&n=50"
    
    # カテゴリがある場合はカテゴリURLを使用
    elif category:
        url = f"https://auctions.yahoo.co.jp/category/{category}?p={page}"
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # エラーがあれば例外を発生
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 商品情報を抽出
        items = []
        product_elements = soup.select('.Product') or soup.select('.productList .product')
        
        for product in product_elements:
            try:
                title_element = product.select_one('.Product__title a') or product.select_one('.Product__title') or product.select_one('.title a')
                title = title_element.text.strip() if title_element else "タイトル不明"
                
                link_element = product.select_one('a.Product__anchor') or product.select_one('.Product__title a') or product.select_one('.title a')
                link = link_element.get('href') if link_element else "#"
                
                price_element = product.select_one('.Product__priceValue') or product.select_one('.Product__price') or product.select_one('.price')
                price = int(re.sub(r'[^0-9]', '', price_element.text.strip())) if price_element else 0
                
                img_element = product.select_one('.Product__imageData') or product.select_one('img')
                img_src = img_element.get('src') if img_element else "/static/no-image.png"
                if not img_src.startswith('http'):
                    img_src = "/static/no-image.png"
                
                time_element = product.select_one('.Product__time') or product.select_one('.remainTime')
                remaining_time = time_element.text.strip() if time_element else "時間不明"
                
                bid_element = product.select_one('.Product__bid') or product.select_one('.bidCount')
                bid_count = int(re.sub(r'[^0-9]', '', bid_element.text.strip())) if bid_element else 0
                
                shipping_fee_element = product.select_one('.Product__postage')
                shipping_fee = shipping_fee_element.text.strip() if shipping_fee_element else "送料未定"
                
                items.append({
                    'title': title,
                    'link': link,
                    'price': price,
                    'img': img_src,
                    'remaining_time': remaining_time,
                    'bid_count': bid_count,
                    'shipping_fee': shipping_fee
                })
            except Exception as e:
                print(f"商品抽出エラー: {e}")
                continue
        
        # 価格範囲でフィルタリング
        if price_min is not None:
            items = [item for item in items if item['price'] >= price_min]
        if price_max is not None:
            items = [item for item in items if item['price'] <= price_max]
        
        # ソート
        if sort_by:
            reverse = sort_order == 'desc'
            if sort_by == 'price':
                items.sort(key=lambda x: x['price'], reverse=reverse)
            elif sort_by == 'time':
                items.sort(key=lambda x: x['remaining_time'], reverse=reverse)
            elif sort_by == 'bids':
                items.sort(key=lambda x: x['bid_count'], reverse=reverse)
        
        # カテゴリ情報を抽出
        categories = []
        category_elements = soup.select('.SearchMode .SearchMode__item') or soup.select('.category li a')
        
        for category_elem in category_elements:
            try:
                category_link = category_elem.get('href', '')
                category_id = re.search(r'/category/(\d+)', category_link)
                if category_id:
                    category_id = category_id.group(1)
                else:
                    continue
                    
                category_name = category_elem.text.strip()
                categories.append({
                    'id': category_id,
                    'name': category_name,
                    'link': category_link
                })
            except Exception as e:
                print(f"カテゴリ抽出エラー: {e}")
                continue
        
        return {
            'items': items,
            'categories': categories,
            'current_page': page,
            'search_query': search_query,
            'category': category,
            'closed': closed,
            'sort_by': sort_by,
            'sort_order': sort_order,
            'price_min': price_min,
            'price_max': price_max,
            'auchours': auchours
        }
        
    except Exception as e:
        print(f"スクレイピングエラー: {e}")
        return {
            'items': [],
            'categories': [],
            'current_page': page,
            'search_query': search_query,
            'category': category,
            'closed': closed,
            'sort_by': sort_by,
            'sort_order': sort_order,
            'price_min': price_min,
            'price_max': price_max,
            'auchours': auchours,
            'error': str(e)
        }

async def get_item_details(url: str):
    """商品詳細情報を取得する関数（スケルトン）"""
    # 実際の実装がない場合のスケルトン関数
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 基本的な商品情報抽出処理をここに実装
        title = soup.select_one('.ProductTitle__text').text.strip() if soup.select_one('.ProductTitle__text') else "タイトル情報なし"
        
        return {
            'title': title,
            'url': url,
            # 他の商品詳細情報
        }
    except Exception as e:
        print(f"商品詳細取得エラー: {e}")
        return {
            'error': str(e),
            'url': url
        }

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """トップページ表示"""
    cache_headers = {
        "Cache-Control": "public, max-age=3600",  # 1時間キャッシュ
    }
    return create_compressed_response(
        "index.html", 
        {"request": request, "data": {"search_query": "", "closed": False}},
        cache_headers
    )

@app.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: Optional[str] = None,
    page: int = 1,
    closed: bool = False,
    sort_by: Optional[str] = None,
    sort_order: str = 'asc',
    price_min: Optional[str] = Query(None),
    price_max: Optional[str] = Query(None),
    auchours: Optional[int] = Query(None)
):
    """検索結果表示"""
    if not q:
        return RedirectResponse(url="/")
    
    # 空文字列を None に変換
    price_min = int(price_min) if price_min and price_min.strip() else None
    price_max = int(price_max) if price_max and price_max.strip() else None
    
    result = await scrape_yahoo_auctions(
        search_query=q,
        page=page,
        closed=closed,
        sort_by=sort_by,
        sort_order=sort_order,
        price_min=price_min,
        price_max=price_max,
        auchours=auchours
    )
    
    # 検索結果は短時間キャッシュ
    cache_headers = {
        "Cache-Control": "public, max-age=600",  # 10分キャッシュ
    }
    return create_compressed_response("search.html", {"request": request, "data": result}, cache_headers)

@app.get("/category/{category_id}", response_class=HTMLResponse)
async def category(request: Request, category_id: str, page: int = 1):
    """カテゴリページ表示"""
    result = await scrape_yahoo_auctions(category=category_id, page=page)
    
    # カテゴリ結果は短時間キャッシュ
    cache_headers = {
        "Cache-Control": "public, max-age=1800",  # 30分キャッシュ
    }
    return create_compressed_response("category.html", {"request": request, "data": result}, cache_headers)

@app.get("/item", response_class=HTMLResponse)
async def item_details(request: Request, url: Optional[str] = None):
    """商品詳細ページ表示"""
    if not url:
        return RedirectResponse(url="/")
    
    item_data = await get_item_details(url)
    
    # 商品詳細は短時間キャッシュ
    cache_headers = {
        "Cache-Control": "public, max-age=300",  # 5分キャッシュ
    }
    return create_compressed_response("item.html", {"request": request, "item": item_data}, cache_headers)

@app.get("/closed", response_class=HTMLResponse)
async def closed_search(request: Request, q: Optional[str] = None, page: int = 1):
    """落札履歴表示"""
    if not q:
        return RedirectResponse(url="/")
    
    result = await scrape_yahoo_auctions(search_query=q, page=page, closed=True)
    
    # 落札履歴は長めにキャッシュ
    cache_headers = {
        "Cache-Control": "public, max-age=3600",  # 1時間キャッシュ
    }
    return create_compressed_response("closed.html", {"request": request, "data": result}, cache_headers)

@app.exception_handler(404)
async def not_found_exception_handler(request: Request, exc: HTTPException):
    return create_compressed_response("404.html", {"request": request}, {"Cache-Control": "public, max-age=86400"})

@app.exception_handler(500)
async def server_error_exception_handler(request: Request, exc: HTTPException):
    return create_compressed_response("500.html", {"request": request}, {"Cache-Control": "no-store"})

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
