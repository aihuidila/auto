#!/bin/bash

# --- 基础配置 ---
CONTAINER_NAME="zhiwen-mysql"
DB_USER="root"
DB_PASS="zhiwen_password"
DB_NAME="ames"

# 数据库执行函数 (隐藏密码警告)
function db_query() {
    sudo docker exec -i $CONTAINER_NAME bash -c "export MYSQL_PWD='$DB_PASS'; mysql -u$DB_USER $DB_NAME -N -s -e \"$1\""
}

function db_update() {
    sudo docker exec -i $CONTAINER_NAME bash -c "export MYSQL_PWD='$DB_PASS'; mysql -u$DB_USER $DB_NAME -e \"$1\""
}

echo "========================================"
echo "   私有化模型启动方式修改工具"
echo "========================================"

# 1. 查询模型
echo "正在提取 source='upload' 的模型列表..."
# 获取数据，使用 | 分隔 name 和 worker_name
RAW_DATA=$(db_query "SELECT CONCAT(name, '|', IFNULL(worker_name,'NULL')) FROM model_store WHERE source='upload';")

if [ -z "$RAW_DATA" ]; then
    echo "未找到任何私有化导入的模型，请检查数据库。"
    exit 0
fi

# 2. 解析数据并显示
models=()
workers=()
i=0
while read -r line; do
    name=$(echo $line | cut -d'|' -f1)
    worker=$(echo $line | cut -d'|' -f2)
    models+=("$name")
    workers+=("$worker")
    echo " [$i] 模型名称: $name"
    echo "     当前 Worker: ${worker}"
    echo "----------------------------------------"
    ((i++))
done <<< "$RAW_DATA"

# 3. 交互输入 (针对 curl | bash 环境优化)
echo -n "请选择模型编号: "
read -r model_idx < /dev/tty

if [[ ! $model_idx =~ ^[0-9]+$ ]] || [ $model_idx -ge ${#models[@]} ]; then
    echo "错误: 输入编号无效。"
    exit 1
fi

target_model=${models[$model_idx]}
target_worker=${workers[$model_idx]}

echo -e "\n请选择新的 backend_type:"
echo " 1) vllm"
echo " 2) ftransformers"
echo " 3) vox-box"
echo -n "请输入选项 (1-3): "
read -r backend_choice < /dev/tty

case $backend_choice in
    1) new_backend="vllm" ;;
    2) new_backend="ftransformers" ;;
    3) new_backend="vox-box" ;;
    *) echo "错误: 选择无效"; exit 1 ;;
esac

# 4. 逻辑判断 worker_name
worker_sql=""
if [ "$target_worker" == "NULL" ] || [ -z "$target_worker" ]; then
    echo ">>> 检测到 worker_name 为空，自动设置为: worker qujing 2"
    worker_sql=", worker_name='worker qujing 2'"
else
    echo ">>> 检测到已有 worker_name，跳过修改。"
fi

# 5. 执行更新
echo -e "\n正在更新数据库..."
final_sql="UPDATE model_store SET backend_type='$new_backend' $worker_sql WHERE name='$target_model' AND source='upload';"
db_update "$final_sql"

if [ $? -eq 0 ]; then
    echo "========================================"
    echo " 修改成功！"
    echo " 模型: $target_model"
    echo " 后端: $new_backend"
    echo "========================================"
else
    echo "更新过程中出现错误，请检查数据库连接。"
fi
