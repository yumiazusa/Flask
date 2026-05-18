# app.py - 项目立项系统主程序（MySQL版）- 优化版
import gc
import math
import os
import sys
import tempfile
import threading
import time
import tracemalloc
from collections import OrderedDict
from datetime import datetime
from queue import Empty, Full, LifoQueue

import pymysql
import xlsxwriter
from flask import (
    Flask,
    after_this_request,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

try:
    import psutil
except ImportError:
    psutil = None


# ==================== 配置区域 ====================
class Config:
    # Flask安全密钥
    SECRET_KEY = 'project-system-2026-secret-change-this-in-production'

    # MySQL配置（根据实际修改！）
    MYSQL_HOST = '47.108.254.13'  # MySQL服务器地址
    MYSQL_PORT = 3306  # MySQL端口，默认3306
    MYSQL_DATABASE = 'projectDB'  # 数据库名
    MYSQL_USERNAME = 'ProjectDB'  # 用户名
    MYSQL_PASSWORD = '4100282Ly@'  # 密码
    MYSQL_CHARSET = 'utf8mb4'  # 字符集

    # 项目号配置
    PROJECT_YEAR = '2026'  # 固定年份部分

    # 评估类型映射（类型名称 -> 三字母代码）
    EVALUATION_TYPES = {
        '资产评估': 'AAP',
        '土地评估': 'LAP',
        '珠宝评估': 'JAP',
        '矿业权评估': 'MRV',
        '咨询': 'ACP'
    }

    # 部门选项
    DEPARTMENTS = [
        '业务1组（房地产）',
        '业务2组（固定资产）',
        '业务3组（企业价值）',
        '矿业权小组',
        '质控部',
        '其他'
    ]

    # Flask配置
    DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    HOST = '0.0.0.0'
    PORT = 5500

    # 列表分页配置
    PAGE_SIZE = 50
    MAX_PAGE_SIZE = 100

    # 低配服务器下的数据库连接池配置
    DB_POOL_SIZE = 4
    DB_POOL_WAIT_TIMEOUT = 5
    DB_POOL_RECYCLE_SECONDS = 1800

    # 缓存与导出控制
    STATS_CACHE_TTL_SECONDS = 30
    STATS_CACHE_MAX_ENTRIES = 16
    EXPORT_FETCH_BATCH_SIZE = 500

    # 运行时内存诊断阈值
    MEMORY_SOFT_LIMIT_MB = 2300
    TRACEMALLOC_FRAMES = 10


# 创建配置实例
config = Config()

# ==================== 创建Flask应用 ====================
app = Flask(__name__)
app.secret_key = config.SECRET_KEY

if not tracemalloc.is_tracing():
    tracemalloc.start(config.TRACEMALLOC_FRAMES)


class TTLCacheLRU:
    """带TTL的轻量LRU缓存，避免统计结果无限驻留内存。"""

    def __init__(self, max_entries=16, ttl_seconds=30):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._store = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        now = time.time()
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return value

    def set(self, key, value):
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)

    def clear(self):
        with self._lock:
            self._store.clear()

    def stats(self):
        with self._lock:
            return {
                'entries': len(self._store),
                'max_entries': self.max_entries,
                'ttl_seconds': self.ttl_seconds
            }


class PooledConnection:
    """将 close() 转换为归还连接池，兼容原有业务代码。"""

    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn
        self._closed = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._conn is not None:
            self._pool.release(self._conn)
            self._conn = None

    def real_close(self):
        if self._closed:
            return
        self._closed = True
        if self._conn is not None:
            self._pool.discard(self._conn)
            self._conn = None

    def __getattr__(self, item):
        if self._conn is None:
            raise RuntimeError('数据库连接已关闭')
        return getattr(self._conn, item)

    def __bool__(self):
        return self._conn is not None


class MySQLConnectionPool:
    """轻量连接池，限制并发连接总数并回收空闲连接。"""

    def __init__(self, max_size=4, wait_timeout=5, recycle_seconds=1800):
        self.max_size = max_size
        self.wait_timeout = wait_timeout
        self.recycle_seconds = recycle_seconds
        self._queue = LifoQueue(maxsize=max_size)
        self._lock = threading.Lock()
        self._created = 0

    def _create_connection(self):
        return pymysql.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USERNAME,
            password=config.MYSQL_PASSWORD,
            database=config.MYSQL_DATABASE,
            charset=config.MYSQL_CHARSET,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=30,
            write_timeout=30,
            autocommit=False
        )

    def _is_usable(self, conn):
        try:
            conn.ping(reconnect=True)
            return True
        except Exception:
            return False

    def acquire(self):
        while True:
            try:
                pooled_at, conn = self._queue.get_nowait()
                if (time.time() - pooled_at) > self.recycle_seconds or not self._is_usable(conn):
                    self.discard(conn)
                    continue
                return PooledConnection(self, conn)
            except Empty:
                break

        with self._lock:
            if self._created < self.max_size:
                conn = self._create_connection()
                self._created += 1
                return PooledConnection(self, conn)

        pooled_at, conn = self._queue.get(timeout=self.wait_timeout)
        if (time.time() - pooled_at) > self.recycle_seconds or not self._is_usable(conn):
            self.discard(conn)
            return self.acquire()
        return PooledConnection(self, conn)

    def release(self, conn):
        try:
            conn.rollback()
        except Exception:
            self.discard(conn)
            return

        if not self._is_usable(conn):
            self.discard(conn)
            return

        try:
            self._queue.put_nowait((time.time(), conn))
        except Full:
            self.discard(conn)

    def discard(self, conn):
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            pass
        with self._lock:
            self._created = max(0, self._created - 1)

    def stats(self):
        return {
            'max_size': self.max_size,
            'created': self._created,
            'idle': self._queue.qsize(),
            'in_use': max(0, self._created - self._queue.qsize())
        }


db_pool = MySQLConnectionPool(
    max_size=config.DB_POOL_SIZE,
    wait_timeout=config.DB_POOL_WAIT_TIMEOUT,
    recycle_seconds=config.DB_POOL_RECYCLE_SECONDS
)
stats_cache = TTLCacheLRU(
    max_entries=config.STATS_CACHE_MAX_ENTRIES,
    ttl_seconds=config.STATS_CACHE_TTL_SECONDS
)


# ==================== MySQL数据库连接函数 ====================
def get_db_connection():
    """获取MySQL数据库连接"""
    try:
        return db_pool.acquire()
    except pymysql.OperationalError as e:
        print(f"[错误] MySQL连接错误: {e}")
        print(f"请检查MySQL服务是否运行在 {config.MYSQL_HOST}:{config.MYSQL_PORT}")
        return None
    except pymysql.InternalError as e:
        print(f"[错误] MySQL数据库错误: {e}")
        print(f"请检查数据库 {config.MYSQL_DATABASE} 是否存在")
        return None
    except Exception as e:
        print(f"[错误] 数据库连接失败: {e}")
        return None


def invalidate_runtime_caches():
    """数据变更后主动清理短期缓存，避免旧对象常驻。"""
    stats_cache.clear()


def get_process_memory_mb():
    """优先读取进程RSS，未安装psutil时退化为Python堆跟踪值。"""
    if psutil is not None:
        try:
            return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)
        except Exception:
            pass

    if tracemalloc.is_tracing():
        current, _peak = tracemalloc.get_traced_memory()
        return round(current / (1024 * 1024), 2)

    return 0.0


def collect_memory_report():
    """输出可直接用于排障的运行时内存信息。"""
    current, peak = tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else (0, 0)
    top_stats = []
    if tracemalloc.is_tracing():
        snapshot = tracemalloc.take_snapshot()
        for stat in snapshot.statistics('lineno')[:8]:
            top_stats.append({
                'trace': str(stat.traceback[0]),
                'size_kb': round(stat.size / 1024, 2),
                'count': stat.count
            })

    return {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'process_memory_mb': get_process_memory_mb(),
        'python_heap_mb': round(current / (1024 * 1024), 2),
        'python_heap_peak_mb': round(peak / (1024 * 1024), 2),
        'gc_counts': gc.get_count(),
        'db_pool': db_pool.stats(),
        'stats_cache': stats_cache.stats(),
        'top_allocations': top_stats
    }


def normalize_record(record):
    """统一把 None 转为空字符串，减少模板与前端判空分支。"""
    if not record:
        return record
    for key in record:
        if record[key] is None:
            record[key] = ''
    return record


def parse_multi_value_arg(name):
    """兼容 checkbox 重复参数与逗号拼接参数。"""
    values = []
    for raw in request.args.getlist(name):
        for item in raw.split(','):
            item = item.strip()
            if item:
                values.append(item)
    return values


def get_filter_state():
    return {
        'types': parse_multi_value_arg('project_type'),
        'departments': parse_multi_value_arg('department'),
        'managers': parse_multi_value_arg('manager'),
        'project_no': request.args.get('project_no', '').strip(),
        'contract_no': request.args.get('contract_no', '').strip(),
        'client': request.args.get('client', '').strip()
    }


def build_project_filter_sql(filters):
    clauses = []
    params = []

    if filters['types']:
        clauses.append("project_type IN ({})".format(', '.join(['%s'] * len(filters['types']))))
        params.extend(filters['types'])
    if filters['departments']:
        clauses.append("department IN ({})".format(', '.join(['%s'] * len(filters['departments']))))
        params.extend(filters['departments'])
    if filters['managers']:
        clauses.append("manager IN ({})".format(', '.join(['%s'] * len(filters['managers']))))
        params.extend(filters['managers'])
    if filters['project_no']:
        clauses.append("project_no LIKE %s")
        params.append(f"%{filters['project_no']}%")
    if filters['contract_no']:
        clauses.append("related_contract_no LIKE %s")
        params.append(f"%{filters['contract_no']}%")
    if filters['client']:
        clauses.append("client LIKE %s")
        params.append(f"%{filters['client']}%")

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    return where_sql, params


def get_dashboard_page():
    try:
        page = int(request.args.get('page', '1'))
    except ValueError:
        page = 1
    return max(page, 1)


def get_page_size():
    try:
        page_size = int(request.args.get('page_size', str(config.PAGE_SIZE)))
    except ValueError:
        page_size = config.PAGE_SIZE
    return min(max(page_size, 10), config.MAX_PAGE_SIZE)


def get_manager_options():
    conn = get_db_connection()
    if not conn:
        return []

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT manager
                FROM projects
                WHERE manager IS NOT NULL AND manager != ''
                ORDER BY manager ASC
            """)
            return [row['manager'] for row in cursor.fetchall()]
    except Exception as e:
        print(f"[负责人] 读取失败: {e}")
        return []
    finally:
        conn.close()


def ensure_runtime_indexes(conn):
    """为高频筛选字段补齐索引，减少全表扫描压力。"""
    desired_indexes = {
        'idx_project_type': "CREATE INDEX idx_project_type ON projects (project_type)",
        'idx_department': "CREATE INDEX idx_department ON projects (department)",
        'idx_manager': "CREATE INDEX idx_manager ON projects (manager)",
        'idx_status': "CREATE INDEX idx_status ON projects (status)",
        'idx_related_contract_no': "CREATE INDEX idx_related_contract_no ON projects (related_contract_no)"
    }

    with conn.cursor() as cursor:
        for index_name, ddl in desired_indexes.items():
            cursor.execute("""
                SELECT COUNT(*) AS count
                FROM information_schema.statistics
                WHERE table_schema = %s
                  AND table_name = 'projects'
                  AND index_name = %s
            """, (config.MYSQL_DATABASE, index_name))
            if cursor.fetchone()['count'] == 0:
                cursor.execute(ddl)
        conn.commit()


# ==================== 数据库检查 ====================
def check_database():
    """检查数据库连接和表结构"""
    conn = get_db_connection()
    if not conn:
        return False, "数据库连接失败"

    try:
        with conn.cursor() as cursor:
            # 检查用户表
            cursor.execute("""
                           SELECT COUNT(*) as count
                           FROM information_schema.tables
                           WHERE table_schema = %s AND table_name = 'users'
                           """, (config.MYSQL_DATABASE,))

            result = cursor.fetchone()
            if result['count'] == 0:
                return False, "用户表不存在"

            # 检查项目表
            cursor.execute("""
                           SELECT COUNT(*) as count
                           FROM information_schema.tables
                           WHERE table_schema = %s AND table_name = 'projects'
                           """, (config.MYSQL_DATABASE,))

            result = cursor.fetchone()
            if result['count'] == 0:
                return False, "项目表不存在"

            # 检查默认管理员用户
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE username = 'admin'")
            result = cursor.fetchone()
            if result['count'] == 0:
                cursor.execute("""
                               INSERT INTO users (username, password, realname, department)
                               VALUES (%s, %s, %s, %s)
                               """, ('admin', 'admin123', '系统管理员', '质控部'))
                conn.commit()
                print("[系统] 已创建默认管理员账号: admin/admin123，部门：质控部")

            ensure_runtime_indexes(conn)

        return True, "数据库检查完成"

    except Exception as e:
        return False, f"数据库检查失败: {str(e)}"
    finally:
        if conn:
            conn.close()


# ==================== 项目号生成函数 ====================
def generate_project_no(project_type):
    """
    生成项目号：2026XXX0001
    规则：
      - 2026: 固定年份
      - XXX: 三字母类型代码
      - 0001: 四位数字序号，按类型独立递增
    """
    # 1. 验证评估类型
    if project_type not in config.EVALUATION_TYPES:
        return None, f"不支持的项目类型: {project_type}"

    # 2. 获取类型代码
    type_code = config.EVALUATION_TYPES[project_type]
    prefix = f"{config.PROJECT_YEAR}{type_code}"

    # 3. 连接数据库
    conn = get_db_connection()
    if not conn:
        return None, "数据库连接失败"

    try:
        with conn.cursor() as cursor:
            # 4. 查找该类型当前最大项目号
            cursor.execute("""
                           SELECT MAX(project_no) as max_no
                           FROM projects
                           WHERE project_no LIKE %s
                           """, (prefix + '%',))

            result = cursor.fetchone()
            max_project_no = result['max_no'] if result['max_no'] else None

            # 5. 计算新序号
            if max_project_no:
                try:
                    # 提取最后4位数字
                    last_seq = int(max_project_no[-4:])
                    new_seq = last_seq + 1

                    # 验证序号范围（不超过9999）
                    if new_seq > 9999:
                        return None, f"项目序号已超过最大限制9999"

                except (ValueError, IndexError):
                    # 如果格式不对，从1开始
                    new_seq = 1
            else:
                # 该类型还没有项目，从0001开始
                new_seq = 1

            # 6. 生成项目号
            project_no = f"{prefix}{new_seq:04d}"

            # 7. 验证项目号唯一性（双重检查）
            cursor.execute("SELECT COUNT(*) as count FROM projects WHERE project_no = %s", (project_no,))
            if cursor.fetchone()['count'] > 0:
                # 如果重复，序号加1再试
                new_seq += 1
                if new_seq > 9999:
                    return None, f"项目序号已超过最大限制9999"
                project_no = f"{prefix}{new_seq:04d}"

        return project_no, None

    except Exception as e:
        return None, f"生成项目号时出错: {str(e)}"
    finally:
        if conn:
            conn.close()


# ==================== 数据统计函数 ====================
def get_statistics():
    """获取项目统计信息"""
    cached_stats = stats_cache.get('dashboard_stats')
    if cached_stats is not None:
        return cached_stats

    conn = get_db_connection()
    if not conn:
        return {}

    try:
        with conn.cursor() as cursor:
            stats = {
                'total': 0,
                'by_type': {},
                'by_department': {},
                'today': 0,
                'month': 0
            }

            # 总项目数
            cursor.execute("SELECT COUNT(*) as count FROM projects")
            stats['total'] = cursor.fetchone()['count'] or 0

            # 预计收费总额 (排除作废项目，单位：万元)
            cursor.execute("SELECT SUM(estimated_fee) / 10000.0 as total_fee FROM projects WHERE status != 'invalid'")
            stats['total_fee'] = cursor.fetchone()['total_fee'] or 0.0

            # 按类型统计
            cursor.execute("""
                           SELECT project_type, COUNT(*) as count
                           FROM projects
                           GROUP BY project_type
                           """)
            for row in cursor.fetchall():
                stats['by_type'][row['project_type']] = row['count']

            # 按部门统计
            cursor.execute("""
                           SELECT department, COUNT(*) as count
                           FROM projects
                           GROUP BY department
                           """)
            for row in cursor.fetchall():
                stats['by_department'][row['department']] = row['count']

            # 今日项目数
            cursor.execute("""
                           SELECT COUNT(*) as count
                           FROM projects
                           WHERE DATE (created_date) = CURDATE()
                           """)
            stats['today'] = cursor.fetchone()['count'] or 0

            # 本月项目数
            cursor.execute("""
                           SELECT COUNT(*) as count
                           FROM projects
                           WHERE YEAR (created_date) = YEAR (CURDATE())
                             AND MONTH (created_date) = MONTH (CURDATE())
                           """)
            stats['month'] = cursor.fetchone()['count'] or 0

            stats_cache.set('dashboard_stats', stats)
            return stats

    except Exception as e:
        print(f"[统计] 错误: {e}")
        return {}
    finally:
        if conn:
            conn.close()


def query_projects_page(filters, page, page_size):
    """按筛选条件分页查询项目，避免一次性加载全表。"""
    conn = get_db_connection()
    if not conn:
        return [], 0

    try:
        where_sql, params = build_project_filter_sql(filters)
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count FROM projects {where_sql}", params)
            total = cursor.fetchone()['count'] or 0
            total_pages = max(1, math.ceil(total / page_size)) if total else 1
            safe_page = min(page, total_pages)
            offset = (safe_page - 1) * page_size

            cursor.execute(f"""
                SELECT id,
                       project_no,
                       project_name,
                       project_type,
                       status,
                       manager,
                       business_execution_partner,
                       department,
                       estimated_fee,
                       client,
                       evaluation_object,
                       project_date,
                       base_date,
                       related_contract_no,
                       remark,
                       DATE_FORMAT(created_date, '%%Y/%%m/%%d %%H:%%i') AS created_date
                FROM projects
                {where_sql}
                ORDER BY created_date DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])
            rows = [normalize_record(project) for project in cursor.fetchall()]
            return rows, total, safe_page
    except Exception as e:
        print(f"[分页查询] 错误: {e}")
        return [], 0, 1
    finally:
        conn.close()


# ==================== Flask路由 ====================

# ---------- 首页/登录 ----------
@app.route('/', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash('请输入用户名和密码', 'error')
            return render_template('login.html')

        # 验证用户
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                                   SELECT id, username, realname, department
                                   FROM users
                                   WHERE username = %s
                                     AND password = %s
                                   """, (username, password))

                    user = cursor.fetchone()

                    if user:
                        # 登录成功
                        session['user_id'] = user['id']
                        session['username'] = user['username']
                        session['realname'] = user['realname']
                        session['department'] = user['department']

                        flash('登录成功！', 'success')
                        return redirect(url_for('dashboard'))
                    else:
                        flash('用户名或密码错误', 'error')

            except Exception as e:
                flash('登录时发生错误', 'error')
                print(f"[登录] 错误: {e}")
            finally:
                conn.close()
        else:
            flash('数据库连接失败', 'error')

    return render_template('login.html')


# ---------- 登出 ----------
@app.route('/logout')
def logout():
    """退出登录"""
    session.clear()
    flash('您已成功退出登录', 'info')
    return redirect(url_for('login'))


# ---------- 主面板 ----------
@app.route('/dashboard')
def dashboard():
    """主面板 - 显示项目列表和创建表单"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    projects = []
    stats = {}
    total_projects = 0
    page_size = get_page_size()
    requested_page = get_dashboard_page()
    filter_state = get_filter_state()
    manager_options = get_manager_options()

    try:
        projects, total_projects, current_page = query_projects_page(filter_state, requested_page, page_size)
        stats = get_statistics()
    except Exception as e:
        print(f"[查询] 错误: {e}")
        current_page = 1
        flash('获取数据时出错', 'error')

    total_pages = max(1, math.ceil(total_projects / page_size)) if total_projects else 1
    pagination = {
        'page': current_page,
        'page_size': page_size,
        'total': total_projects,
        'total_pages': total_pages,
        'has_prev': current_page > 1,
        'has_next': current_page < total_pages,
        'start': ((current_page - 1) * page_size + 1) if total_projects else 0,
        'end': min(current_page * page_size, total_projects)
    }

    return render_template('dashboard.html',
                           user=session,
                           projects=projects,
                           total_projects=total_projects,
                           stats=stats,
                           evaluation_types=config.EVALUATION_TYPES,
                           departments=config.DEPARTMENTS,
                           project_year=config.PROJECT_YEAR,
                           manager_options=manager_options,
                           filter_state=filter_state,
                           pagination=pagination)


@app.route('/api/check_duplicate')
def api_check_duplicate():
    """API: 检查项目是否重复"""
    if 'user_id' not in session:
        return jsonify({'error': '未登录'}), 401

    project_name = request.args.get('project_name', '').strip()
    client = request.args.get('client', '').strip()
    estimated_fee = request.args.get('estimated_fee', '').strip()

    if not estimated_fee:
        return jsonify({'success': True, 'duplicates': []})

    try:
        fee_val = float(estimated_fee)
    except ValueError:
        return jsonify({'success': True, 'duplicates': []})

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': '数据库连接失败'}), 500

    try:
        with conn.cursor() as cursor:
            # 查询疑似重复的项目
            # 规则：(委托方 + 金额) OR (项目名称 + 金额)
            cursor.execute("""
                SELECT 
                    id, project_no, project_name, client, estimated_fee, 
                    manager, project_type, status,
                    DATE_FORMAT(created_date, '%%Y-%%m-%%d %%H:%%i') as created_date
                FROM projects 
                WHERE (client = %s AND ABS(estimated_fee - %s) < 0.01)
                   OR (project_name = %s AND ABS(estimated_fee - %s) < 0.01)
                ORDER BY created_date DESC
            """, (client, fee_val, project_name, fee_val))
            
            duplicates = cursor.fetchall()
            
            # 处理数据格式
            for p in duplicates:
                p['estimated_fee'] = float(p['estimated_fee'])
                
            return jsonify({
                'success': True, 
                'duplicates': duplicates,
                'count': len(duplicates)
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ---------- 创建项目 ----------
@app.route('/project/create', methods=['POST'])
def create_project():
    """创建新项目"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        # 获取表单数据
        project_type = request.form.get('project_type', '').strip()
        project_name = request.form.get('project_name', '').strip()
        manager = request.form.get('manager', '').strip()
        business_execution_partner = request.form.get('business_execution_partner', '').strip()

        # 验证必要字段
        if not project_type or project_type not in config.EVALUATION_TYPES:
            flash('请选择有效的评估类型', 'error')
            return redirect(url_for('dashboard'))

        if not project_name:
            flash('请输入项目名称', 'error')
            return redirect(url_for('dashboard'))

        if not manager:
            flash('请输入项目负责人', 'error')
            return redirect(url_for('dashboard'))

        if not business_execution_partner:
            flash('请输入业务执行合伙人', 'error')
            return redirect(url_for('dashboard'))

        # 验证新增的必填字段
        required_fields = {
            'client': '委托方名称',
            'evaluation_object': '评估对象',
            'evaluation_scope': '评估范围',
            'purpose': '经济行为目的'
        }

        for field, name in required_fields.items():
            if not request.form.get(field, '').strip():
                flash(f'请输入{name}', 'error')
                return redirect(url_for('dashboard'))
        
        # 检查是否为强制提交（即已确认重复风险）
        is_forced = request.form.get('force_submit') == '1'
        if is_forced:
            # 实际应用中可写入数据库日志表
            print(f"[日志] 用户 {session.get('username')} 强制提交了疑似重复项目: {project_name}, 委托方: {request.form.get('client')}")

        # 生成项目号
        project_no, error_msg = generate_project_no(project_type)
        if error_msg:
            flash(f'创建失败：{error_msg}', 'error')
            return redirect(url_for('dashboard'))

        # 获取类型代码
        type_code = config.EVALUATION_TYPES[project_type]

        # 处理预计收费金额字段
        estimated_fee = request.form.get('estimated_fee', '0').strip()
        try:
            estimated_fee = float(estimated_fee) if estimated_fee else 0.0
        except ValueError:
            estimated_fee = 0.0

        # 处理日期字段
        project_date = request.form.get('project_date') or None
        base_date = request.form.get('base_date') or None
        related_contract_no = request.form.get('related_contract_no', '').strip()
        remark = request.form.get('remark', '').strip()

        # 插入数据库
        conn = get_db_connection()
        if not conn:
            flash('数据库连接失败', 'error')
            return redirect(url_for('dashboard'))

        try:
            with conn.cursor() as cursor:
                # 插入新项目
                cursor.execute("""
                               INSERT INTO projects
                               (project_no, project_name, project_type, type_code, status,
                                manager, business_execution_partner, department, estimated_fee, project_date, base_date,
                                client, evaluation_object, evaluation_scope, purpose, related_contract_no, remark,
                                created_by)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                               """, (
                                   project_no,
                                   project_name,
                                   project_type,
                                   type_code,
                                   'active',
                                   manager,
                                   business_execution_partner,
                                   request.form.get('department', '').strip(),
                                   estimated_fee,
                                   project_date,
                                   base_date,
                                   request.form.get('client', '').strip(),
                                   request.form.get('evaluation_object', '').strip(),
                                   request.form.get('evaluation_scope', '').strip(),
                                   request.form.get('purpose', '').strip(),
                                   related_contract_no,
                                   remark,
                                   session['username']
                               ))

                conn.commit()
                invalidate_runtime_caches()
                flash(f'✅ 项目创建成功！项目号：{project_no}', 'success')

        except pymysql.IntegrityError as e:
            if 'Duplicate entry' in str(e):
                flash('项目号已存在，请重试', 'error')
            else:
                flash('数据库约束错误', 'error')
            conn.rollback()
        except Exception as e:
            flash(f'创建失败：{str(e)}', 'error')
            conn.rollback()
            print(f"[创建项目] 错误: {e}")
        finally:
            conn.close()

    except Exception as e:
        flash(f'系统错误：{str(e)}', 'error')
        print(f"[创建项目] 系统错误: {e}")

    return redirect(url_for('dashboard'))


# ---------- 编辑项目 ----------
@app.route('/project/<int:project_id>/edit', methods=['POST'])
def edit_project(project_id):
    """编辑项目信息 (AJAX)"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    # 获取并验证必填字段
    project_name = request.form.get('project_name', '').strip()
    manager = request.form.get('manager', '').strip()
    business_execution_partner = request.form.get('business_execution_partner', '').strip()
    client = request.form.get('client', '').strip()

    if not project_name or not manager or not business_execution_partner or not client:
        return jsonify({'success': False, 'error': '请填写所有必填字段'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '数据库连接失败'}), 500

    try:
        with conn.cursor() as cursor:
            # 检查项目状态，如果已作废则不允许编辑
            cursor.execute("SELECT status FROM projects WHERE id = %s", (project_id,))
            project = cursor.fetchone()
            if project and project['status'] == 'invalid':
                return jsonify({'success': False, 'error': '该项目已作废，不可编辑'}), 403

            # 更新项目信息
            cursor.execute("""
                           UPDATE projects
                           SET project_name      = %s,
                               manager           = %s,
                               business_execution_partner = %s,
                               department        = %s,
                               estimated_fee     = %s,
                               project_date      = %s,
                               base_date         = %s,
                               client            = %s,
                               evaluation_object = %s,
                               evaluation_scope  = %s,
                               purpose           = %s,
                               related_contract_no = %s,
                               remark            = %s,
                               updated_date      = NOW()
                           WHERE id = %s
                           """, (
                               project_name,
                               manager,
                               business_execution_partner,
                               request.form.get('department', '').strip(),
                               float(request.form.get('estimated_fee', '0') or 0),
                               request.form.get('project_date') or None,
                               request.form.get('base_date') or None,
                               client,
                               request.form.get('evaluation_object', '').strip(),
                               request.form.get('evaluation_scope', '').strip(),
                               request.form.get('purpose', '').strip(),
                               request.form.get('related_contract_no', '').strip(),
                               request.form.get('remark', '').strip(),
                               project_id
                           ))

            conn.commit()
            invalidate_runtime_caches()
            
            # 返回更新后的数据用于前端刷新UI
            cursor.execute("""
                           SELECT id, project_no, project_name, project_type, status, manager, 
                                  business_execution_partner, department, estimated_fee, client,
                                  related_contract_no, remark, project_date, base_date,
                                  DATE_FORMAT(created_date, '%%Y/%%m/%%d %%H:%%i') as created_date
                           FROM projects WHERE id = %s
                           """, (project_id,))
            updated_project = cursor.fetchone()
            
            return jsonify({'success': True, 'message': '项目更新成功！', 'project': updated_project})

    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': f'更新失败：{str(e)}'}), 500
    finally:
        conn.close()


# ---------- 删除项目 ----------
@app.route('/project/<int:project_id>/delete')
def delete_project(project_id):
    """删除项目 (同步路由，保留用于兼容，但前端现在使用 AJAX)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    if not conn:
        flash('数据库连接失败', 'error')
        return redirect(url_for('dashboard'))

    try:
        with conn.cursor() as cursor:
            # 执行删除前的末位校验
            cursor.execute("SELECT project_no, project_type FROM projects WHERE id = %s", (project_id,))
            project = cursor.fetchone()
            if not project:
                flash('项目不存在', 'error')
                return redirect(url_for('dashboard'))

            # 查找同类型下是否有更新的项目号
            cursor.execute("""
                SELECT COUNT(*) as count FROM projects 
                WHERE project_type = %s AND project_no > %s
            """, (project['project_type'], project['project_no']))
            
            if cursor.fetchone()['count'] > 0:
                flash('该项目编号后续已有项目创建，不可删除。如需停用，请使用“作废”功能。', 'error')
                return redirect(url_for('dashboard'))

            # 删除项目
            cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
            conn.commit()
            invalidate_runtime_caches()
            flash(f'项目 {project["project_no"]} 已删除', 'info')

    except Exception as e:
        flash(f'删除失败：{str(e)}', 'error')
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for('dashboard'))


@app.route('/api/project/<int:project_id>/invalidate', methods=['POST'])
def api_invalidate_project(project_id):
    """API: 作废项目"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '数据库连接失败'}), 500

    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE projects SET status = 'invalid' WHERE id = %s", (project_id,))
            conn.commit()
            invalidate_runtime_caches()
            return jsonify({'success': True, 'message': '项目已作废'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/project/<int:project_id>/check_delete')
def api_check_delete(project_id):
    """API: 检查是否可以删除（是否为末位编号）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '数据库连接失败'}), 500

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT project_no, project_type FROM projects WHERE id = %s", (project_id,))
            project = cursor.fetchone()
            if not project:
                return jsonify({'success': False, 'error': '项目不存在'})

            # 查找同类型下是否有更新的项目号
            cursor.execute("""
                SELECT COUNT(*) as count FROM projects 
                WHERE project_type = %s AND project_no > %s
            """, (project['project_type'], project['project_no']))
            
            can_delete = cursor.fetchone()['count'] == 0
            return jsonify({
                'success': True, 
                'can_delete': can_delete,
                'project_no': project['project_no']
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@app.route('/api/project/<int:project_id>/delete', methods=['POST'])
def api_delete_project(project_id):
    """API: 删除项目 (AJAX)"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '数据库连接失败'}), 500

    try:
        with conn.cursor() as cursor:
            # 再次校验
            cursor.execute("SELECT project_no, project_type FROM projects WHERE id = %s", (project_id,))
            project = cursor.fetchone()
            if not project:
                return jsonify({'success': False, 'error': '项目不存在'})

            cursor.execute("""
                SELECT COUNT(*) as count FROM projects 
                WHERE project_type = %s AND project_no > %s
            """, (project['project_type'], project['project_no']))
            
            if cursor.fetchone()['count'] > 0:
                return jsonify({'success': False, 'error': '该项目编号后续已有项目创建，不可删除。'})

            cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
            conn.commit()
            invalidate_runtime_caches()
            return jsonify({'success': True, 'message': f'项目 {project["project_no"]} 已删除'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


# ---------- 导出Excel ----------
@app.route('/export/projects')
def export_projects():
    """按筛选条件分批导出项目列表，避免DataFrame占用大量内存。"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    if not conn:
        flash('数据库连接失败', 'error')
        return redirect(url_for('dashboard'))

    try:
        filters = get_filter_state()
        where_sql, params = build_project_filter_sql(filters)

        with conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count FROM projects {where_sql}", params)
            total_rows = cursor.fetchone()['count'] or 0

        if total_rows == 0:
            flash('没有可导出的数据', 'info')
            return redirect(url_for('dashboard'))

        columns = [
            ('project_no', '项目号'),
            ('project_name', '项目名称'),
            ('client', '委托方'),
            ('project_type', '评估类型'),
            ('manager', '项目负责人'),
            ('business_execution_partner', '业务执行合伙人'),
            ('related_contract_no', '关联合同号'),
            ('department', '所属部门'),
            ('estimated_fee', '预计收费金额'),
            ('status_text', '状态'),
            ('project_date', '立项日期'),
            ('base_date', '评估基准日'),
            ('evaluation_object', '评估对象'),
            ('evaluation_scope', '评估范围'),
            ('purpose', '经济行为目的'),
            ('remark', '备注'),
            ('created_by', '创建人'),
            ('created_date', '创建时间')
        ]
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
        temp_file.close()

        workbook = xlsxwriter.Workbook(temp_file.name, {'constant_memory': True})
        worksheet = workbook.add_worksheet('项目列表')
        header_format = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1})
        text_wrap_format = workbook.add_format({'text_wrap': True, 'valign': 'top'})
        money_format = workbook.add_format({'num_format': '#,##0.00'})

        column_widths = {'序号': len('序号') + 2}
        worksheet.write(0, 0, '序号', header_format)
        for col_idx, (_field_name, header) in enumerate(columns, start=1):
            worksheet.write(0, col_idx, header, header_format)
            column_widths[header] = len(header) + 2

        stream_conn = get_db_connection()
        if not stream_conn:
            workbook.close()
            os.unlink(temp_file.name)
            flash('数据库连接失败', 'error')
            return redirect(url_for('dashboard'))

        try:
            with stream_conn.cursor(pymysql.cursors.SSDictCursor) as cursor:
                cursor.execute(f"""
                    SELECT project_no,
                           project_name,
                           client,
                           project_type,
                           manager,
                           business_execution_partner,
                           related_contract_no,
                           department,
                           estimated_fee,
                           CASE
                               WHEN status = 'active' THEN '有效'
                               WHEN status = 'invalid' THEN '已作废'
                               ELSE status
                           END AS status_text,
                           project_date,
                           base_date,
                           evaluation_object,
                           evaluation_scope,
                           purpose,
                           remark,
                           created_by,
                           DATE_FORMAT(created_date, '%%Y-%%m-%%d %%H:%%i:%%s') AS created_date
                    FROM projects
                    {where_sql}
                    ORDER BY created_date DESC
                """, params)

                row_idx = 1
                seq_no = total_rows
                while True:
                    batch = cursor.fetchmany(config.EXPORT_FETCH_BATCH_SIZE)
                    if not batch:
                        break

                    for row in batch:
                        worksheet.write(row_idx, 0, seq_no)
                        column_widths['序号'] = max(column_widths['序号'], len(str(seq_no)) + 2)
                        seq_no -= 1

                        for col_idx, (field_name, header) in enumerate(columns, start=1):
                            value = row.get(field_name, '')
                            if value is None:
                                value = ''

                            cell_format = text_wrap_format
                            if field_name == 'estimated_fee' and value not in ('', None):
                                worksheet.write_number(row_idx, col_idx, float(value), money_format)
                                value_for_width = f"{float(value):,.2f}"
                            else:
                                value_for_width = str(value)
                                worksheet.write(row_idx, col_idx, value_for_width, cell_format)

                            column_widths[header] = min(max(column_widths[header], len(value_for_width) + 2), 50)

                        row_idx += 1
        finally:
            stream_conn.close()

        for col_idx, header in enumerate(['序号'] + [header for _field_name, header in columns]):
            worksheet.set_column(col_idx, col_idx, column_widths[header])

        workbook.close()

        timestamp = datetime.now().strftime('%Y%m%d')
        filename = f"项目列表_导出_{timestamp}.xlsx"

        @after_this_request
        def remove_temp_export_file(response):
            try:
                os.remove(temp_file.name)
            except OSError:
                pass
            return response

        return send_file(
            temp_file.name,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        print(f"[导出] 错误: {e}")
        flash(f'导出失败：{str(e)}', 'error')
        return redirect(url_for('dashboard'))
    finally:
        conn.close()


@app.route('/admin/system/memory')
def admin_system_memory():
    """管理员查看运行时内存快照，用于排查内存持续上涨问题。"""
    if 'user_id' not in session:
        return jsonify({'error': '未登录'}), 401
    if session.get('username') != 'admin':
        return jsonify({'error': '无权限'}), 403
    return jsonify({'success': True, 'report': collect_memory_report()})


# ---------- API接口 ----------
@app.route('/api/next_project_no/<project_type>')
def api_next_project_no(project_type):
    """API：获取下一个项目号（用于AJAX预览）"""
    if 'user_id' not in session:
        return jsonify({'error': '未登录'}), 401

    if project_type not in config.EVALUATION_TYPES:
        return jsonify({'error': '不支持的项目类型'}), 400

    project_no, error_msg = generate_project_no(project_type)

    if error_msg:
        return jsonify({'error': error_msg}), 500

    return jsonify({
        'success': True,
        'project_no': project_no,
        'type_code': config.EVALUATION_TYPES[project_type]
    })


@app.route('/api/project/<int:project_id>')
def api_get_project(project_id):
    """API：获取项目详情（用于AJAX编辑）"""
    if 'user_id' not in session:
        return jsonify({'error': '未登录'}), 401

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': '数据库连接失败'}), 500

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                           SELECT id,
                                  project_no,
                                  project_name,
                                  project_type,
                                  manager,
                                  business_execution_partner,
                                  department,
                                  estimated_fee,
                                  DATE_FORMAT(project_date, '%%Y-%%m-%%d') as project_date,
                                  DATE_FORMAT(base_date, '%%Y-%%m-%%d')    as base_date,
                                  client,
                                  evaluation_object,
                                  evaluation_scope,
                                  purpose,
                                  related_contract_no,
                                  remark
                           FROM projects
                           WHERE id = %s
                           """, (project_id,))

            project = cursor.fetchone()
            if not project:
                return jsonify({'error': '项目不存在'}), 404

            # 转换None值为空字符串
            for key in project:
                if project[key] is None:
                    project[key] = ''

            return jsonify({'success': True, 'project': project})

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


# ---------- 添加用户 ----------
@app.route('/user/add', methods=['POST'])
def add_user():
    """添加新用户（仅管理员）"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # 权限检查：只有admin可以添加用户
    if session.get('username') != 'admin':
        flash('只有管理员可以添加用户', 'error')
        return redirect(url_for('dashboard'))

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    realname = request.form.get('realname', '').strip()
    department = request.form.get('department', '').strip()

    if not username or not password:
        flash('用户名和密码不能为空', 'error')
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    if not conn:
        flash('数据库连接失败', 'error')
        return redirect(url_for('dashboard'))

    try:
        with conn.cursor() as cursor:
            # 检查用户名是否已存在
            cursor.execute("SELECT COUNT(*) as count FROM users WHERE username = %s", (username,))
            if cursor.fetchone()['count'] > 0:
                flash('用户名已存在', 'error')
                return redirect(url_for('dashboard'))

            # 插入新用户
            cursor.execute("""
                           INSERT INTO users (username, password, realname, department)
                           VALUES (%s, %s, %s, %s)
                           """, (username, password, realname, department))

            conn.commit()
            invalidate_runtime_caches()
            flash(f'用户 {realname} 添加成功', 'success')

    except Exception as e:
        flash(f'添加失败：{str(e)}', 'error')
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for('dashboard'))


@app.after_request
def trim_runtime_memory(response):
    """内存逼近阈值时主动清缓存并触发GC，降低持续爬升风险。"""
    if get_process_memory_mb() >= config.MEMORY_SOFT_LIMIT_MB:
        invalidate_runtime_caches()
        gc.collect()
    return response


# ==================== MySQL建表SQL ====================
def get_mysql_create_table_sql():
    """获取MySQL建表SQL语句"""
    return """
           -- 创建数据库
           CREATE \
           DATABASE IF NOT EXISTS ProjectDB DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE \
           ProjectDB;

-- 用户表
           CREATE TABLE IF NOT EXISTS users \
           ( \
               id \
               INT \
               AUTO_INCREMENT \
               PRIMARY \
               KEY, \
               username \
               VARCHAR \
           ( \
               50 \
           ) UNIQUE NOT NULL,
               password VARCHAR \
           ( \
               100 \
           ) NOT NULL,
               realname VARCHAR \
           ( \
               50 \
           ),
               department VARCHAR \
           ( \
               100 \
           ),
               created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
               ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE =utf8mb4_unicode_ci;

-- 项目表（优化版）
           CREATE TABLE IF NOT EXISTS projects \
           ( \
               id \
               INT \
               AUTO_INCREMENT \
               PRIMARY \
               KEY, \
               project_no \
               VARCHAR \
           ( \
               50 \
           ) UNIQUE NOT NULL COMMENT '项目号，如：2026AAP0001',
               project_name VARCHAR \
           ( \
               200 \
           ) NOT NULL,
               project_type VARCHAR \
           ( \
               50 \
           ) COMMENT '评估类型：资产评估、土地评估、珠宝评估、矿业权评估、咨询',
               status VARCHAR(20) DEFAULT 'active' COMMENT '项目状态：active-有效, invalid-作废',
               type_code VARCHAR \
           ( \
               3 \
           ) COMMENT '类型代码：AAP、LAP、JAP、MRV、ACP',
               manager VARCHAR \
           ( \
               100 \
           ) NOT NULL,
               business_execution_partner VARCHAR \
           ( \
               100 \
           ) COMMENT '业务执行合伙人',
               department VARCHAR \
           ( \
               100 \
           ) COMMENT '业务1组（房地产）、业务2组（固定资产）、业务3组（企业价值）、质控部、其他',
               estimated_fee DECIMAL \
           ( \
               18, \
               2 \
           ) COMMENT '预计收费金额',
               project_date DATE COMMENT '立项日期',
               base_date DATE COMMENT '评估基准日',
               client VARCHAR \
           ( \
               200 \
           ) NOT NULL COMMENT '委托方名称',
               evaluation_object TEXT NOT NULL COMMENT '评估对象',
               evaluation_scope TEXT NOT NULL COMMENT '评估范围',
               purpose TEXT NOT NULL COMMENT '经济行为目的',
               related_contract_no VARCHAR(100) COMMENT '关联合同号',
               remark TEXT COMMENT '备注',
               created_by VARCHAR \
           ( \
               50 \
           ),
               created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
               ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE =utf8mb4_unicode_ci;

-- 创建索引
           CREATE INDEX idx_project_no ON projects (project_no);
           CREATE INDEX idx_type_code ON projects (type_code);
           CREATE INDEX idx_created_date ON projects (created_date);
           CREATE INDEX idx_client ON projects (client); \
           """


# ==================== 启动应用 ====================
if __name__ == '__main__':
    print("=" * 60)
    print("项目立项系统 - MySQL版（优化版）")
    print("=" * 60)

    # 显示MySQL建表SQL
    print("[MySQL] 建表SQL:")
    print(get_mysql_create_table_sql())
    print()

    # 检查模板文件夹
    templates_dir = 'templates'
    if not os.path.exists(templates_dir):
        print(f"[系统] 创建templates文件夹")
        os.makedirs(templates_dir, exist_ok=True)

        # 创建基本的登录页面
        login_html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>项目立项系统 - 登录</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Microsoft YaHei', sans-serif;
            background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%);
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 15px 35px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 450px;
        }
        .login-header {
            text-align: center;
            margin-bottom: 20px;
        }
        .login-header h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }
        .login-header p {
            color: #666;
            font-size: 14px;
            line-height: 1.6;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 500;
        }
        .form-group input {
            width: 100%;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 6px;
            font-size: 16px;
        }
        .form-group input:focus {
            outline: none;
            border-color: #3498db;
        }
        .login-btn {
            width: 100%;
            padding: 14px;
            background: #3498db;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 10px;
        }
        .login-btn:hover {
            background: #2980b9;
        }
        .alert {
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
        .alert-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .alert-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .system-notice {
            margin-top: 25px;
            padding: 15px;
            background: #fff3cd;
            border-radius: 6px;
            text-align: left;
            font-size: 13px;
            color: #856404;
            border: 1px solid #ffeaa7;
        }
        .system-notice h4 {
            margin-top: 0;
            margin-bottom: 8px;
            color: #856404;
        }
        .system-notice ul {
            margin: 10px 0;
            padding-left: 20px;
        }
        .system-notice li {
            margin-bottom: 5px;
        }
        .default-account {
            margin-top: 15px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 6px;
            text-align: center;
            font-size: 13px;
            color: #666;
        }
        .system-version {
            margin-top: 15px;
            text-align: center;
            font-size: 12px;
            color: #999;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-header">
            <h1>项目立项管理系统</h1>
            <p>项目号规则：2026XXX0001</p>
        </div>

        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ 'error' if category == 'error' else category }}">
                        {{ message }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <form method="POST" action="/">
            <div class="form-group">
                <label>用户名</label>
                <input type="text" name="username" required placeholder="请输入用户名">
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" name="password" required placeholder="请输入密码">
            </div>
            <button type="submit" class="login-btn">登录系统</button>
        </form>

        <div class="system-notice">
            <h4>⚠️ 重要提示：</h4>
            <ul>
                <li><strong>本临时系统用于中和金乾非房地产项目的立项工作，房地产项目则在共享表格中进行操作，提醒系统使用人注意。</strong></li>
            </ul>
        </div>

        <div class="system-version">
            <p>© 2026 项目立项系统 | 临时版本 1.0</p>
        </div>
    </div>
</body>
</html>'''

        with open(os.path.join(templates_dir, 'login.html'), 'w', encoding='utf-8') as f:
            f.write(login_html)
        print("[模板] 已创建登录页面")

    # 检查数据库
    print("[系统] 正在检查数据库...")
    success, message = check_database()

    if success:
        print(f"[系统] {message}")

        # 显示项目号规则
        print(f"\n[规则] 项目号格式: {config.PROJECT_YEAR}XXX0001")
        print("评估类型映射:")
        for eval_type, code in config.EVALUATION_TYPES.items():
            print(f"  {eval_type:10} -> {code}")

        print(f"\n[部门] 可选部门:")
        for dept in config.DEPARTMENTS:
            print(f"  {dept}")

        print(f"\n[系统] 启动成功！")
        print(f"[系统] 访问地址: http://localhost:{config.PORT}")
        print(f"[系统] 默认账号: admin / admin123（部门：质控部）")
        print("[系统] 提示：请先使用管理员账号添加用户，然后使用添加的用户登录")
        print("=" * 60)

        # 启动Flask应用
        app.run(
            debug=config.DEBUG,
            host=config.HOST,
            port=config.PORT,
            threaded=True
        )
    else:
        print(f"[系统] {message}")
        print("\n[检查] 请确认以下事项:")
        print("  1. MySQL配置是否正确")
        print("  2. MySQL服务是否运行")
        print("  3. 数据库和表是否已创建")
        print("  4. 数据库用户权限是否足够")
        print("\n[SQL] 请执行以下SQL创建数据库和表:")
        print(get_mysql_create_table_sql())
        print("=" * 60)
        sys.exit(1)
