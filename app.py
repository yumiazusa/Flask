# app.py - 项目立项系统主程序（MySQL版）- 优化版
import pymysql
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from datetime import datetime
import os
import sys
import pandas as pd
import io
from flask import send_file


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
    DEBUG = True
    HOST = '0.0.0.0'
    PORT = 5500


# 创建配置实例
config = Config()

# ==================== 创建Flask应用 ====================
app = Flask(__name__)
app.secret_key = config.SECRET_KEY


# ==================== MySQL数据库连接函数 ====================
def get_db_connection():
    """获取MySQL数据库连接"""
    try:
        conn = pymysql.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USERNAME,
            password=config.MYSQL_PASSWORD,
            database=config.MYSQL_DATABASE,
            charset=config.MYSQL_CHARSET,
            cursorclass=pymysql.cursors.DictCursor  # 返回字典格式的结果
        )
        return conn

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

            return stats

    except Exception as e:
        print(f"[统计] 错误: {e}")
        return {}
    finally:
        if conn:
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

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                # 获取项目列表（包含状态字段）
                cursor.execute("""
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
                                      DATE_FORMAT(created_date, '%Y/%m/%d %H:%i') as created_date
                               FROM projects
                               ORDER BY created_date DESC
                               """)

                # 直接获取字典格式的结果
                projects = cursor.fetchall()

                # 转换None值为空字符串
                for project in projects:
                    for key in project:
                        if project[key] is None:
                            project[key] = ''

            # 获取统计信息
            stats = get_statistics()

        except Exception as e:
            print(f"[查询] 错误: {e}")
            flash('获取数据时出错', 'error')
        finally:
            conn.close()

    return render_template('dashboard.html',
                           user=session,
                           projects=projects,
                           stats=stats,
                           evaluation_types=config.EVALUATION_TYPES,
                           departments=config.DEPARTMENTS,
                           project_year=config.PROJECT_YEAR)


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
            return jsonify({'success': True, 'message': f'项目 {project["project_no"]} 已删除'})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


# ---------- 导出Excel ----------
@app.route('/export/projects')
def export_projects():
    """导出项目列表到Excel"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    if not conn:
        flash('数据库连接失败', 'error')
        return redirect(url_for('dashboard'))

    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                           SELECT 
                                  project_no as '项目号',
                                  project_name as '项目名称',
                                  client as '委托方',
                                  project_type as '评估类型',
                                  manager as '项目负责人',
                                  business_execution_partner as '业务执行合伙人',
                                  related_contract_no as '关联合同号',
                                  department as '所属部门',
                                  estimated_fee as '预计收费金额',
                                  project_date as '立项日期',
                                  base_date as '评估基准日',
                                  evaluation_object as '评估对象',
                                  evaluation_scope as '评估范围',
                                  purpose as '经济行为目的',
                                  remark as '备注',
                                  created_by as '创建人',
                                  DATE_FORMAT(created_date, '%Y-%m-%d %H:%i:%s') as '创建时间'
                           FROM projects
                           ORDER BY created_date DESC
                           """)
            projects = cursor.fetchall()

        if not projects:
            flash('没有可导出的数据', 'info')
            return redirect(url_for('dashboard'))

        # 转换为DataFrame
        df = pd.DataFrame(projects)
        
        # 增加自然序号列 (倒序序号：最新条目序号最大)
        df.insert(0, '序号', range(len(df), 0, -1))

        # 创建内存中的字节流
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='项目列表')
            
            # 获取xlsxwriter对象以设置样式
            workbook = writer.book
            worksheet = writer.sheets['项目列表']
            
            # 设置列宽
            header_format = workbook.add_format({'bold': True, 'bg_color': '#D7E4BC', 'border': 1})
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                # 改进的列宽计算，处理可能出现的类型错误
                try:
                    # 获取该列所有值的最大长度
                    max_val_len = df[value].apply(lambda x: len(str(x)) if pd.notnull(x) else 0).max()
                    # 与表头长度比较
                    column_len = max(max_val_len, len(str(value))) + 2
                except:
                    # 如果计算失败，使用默认宽度
                    column_len = 20
                worksheet.set_column(col_num, col_num, min(column_len, 50))

        output.seek(0)
        
        # 文件命名：项目列表_导出_YYYYMMDD.xlsx
        timestamp = datetime.now().strftime('%Y%m%d')
        filename = f"项目列表_导出_{timestamp}.xlsx"

        return send_file(
            output,
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
            flash(f'用户 {realname} 添加成功', 'success')

    except Exception as e:
        flash(f'添加失败：{str(e)}', 'error')
        conn.rollback()
    finally:
        conn.close()

    return redirect(url_for('dashboard'))


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
                <li>登录人员请先使用管理员账号登录，添加用户后，切换至添加的用户登录</li>
                <li>本系统为临时使用，待正式系统上线后不再使用！</li>
                <li>系统管理员账号可以多人同时登录</li>
            </ul>
        </div>

        <div class="default-account">
            <p>默认管理员账号</p>
            <p>用户名: <strong>admin</strong></p>
            <p>密码: <strong>admin123</strong></p>
            <p>部门: <strong>质控部</strong></p>
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