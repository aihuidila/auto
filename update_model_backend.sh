#!/bin/bash

# --- 配置参数 ---
CONTAINER_NAME="zhiwen-mysql"
DB_USER="root"
DB_PASS="zhiwen_password"
DB_NAME="ames"

# 定义执行数据库命令的函数，避免警告
function mysql_exec() {
    local sql_cmd=$1
    # 使用 bash -c 配合 MYSQL_PWD 环境变量隐藏密码
    sudo docker exec -i $CONTAINER_NAME bash -c "export MYSQL_PWD='$DB_PASS'; mysql -u$DB_USER $DB_NAME $sql_cmd"
}

# 1. 查询私有化导入的模型 (source='upload')
echo "--------------------------------"
echo "正在从数据库查询私有化导入的模型..."
MODELS_DATA=$(mysql_exec "-N -e \"SELECT name, worker_name FROM model_store WHERE source='upload';\"")

if [ -z "$MODELS_DATA" ]; then
    echo "未找到 source='upload' 的模型，退出。"
    exit 0
fi

# 将查询结果转换为数组
model_names=()
worker_names=()
while read -r name worker; do
    model_names+=("$name")
    worker_names+=("$worker")
done <<< "$MODELS_DATA"

# 2. 选择对应模型
echo "--------------------------------"
echo "查询到以下私有化模型，请输入编号进行修改:"
for i in "${!model_names[@]}"; do
    # 如果 worker 为空，显示为 "未设置"
    current_worker=${worker_names[$i]}
    if [ "$current_worker" == "NULL" ] || [ -z "$current_worker" ]; then
        current_worker="[未设置]"
    fi
    printf "[%d] 模型名: %-25s | 当前 Worker: %s\n" "$i" "${model_names[$i]}" "$current_worker"
done

read -p "请输入模型编号: " model_idx

# 校验输入
if [[ ! $model_idx =~ ^[0-9]+$ ]] || [ $model_idx -ge ${#model_names[@]} ]; then
    echo "无效的选择，程序结束。"
    exit 1
fi

SELECTED_MODEL=${model_names[$model_idx]}
SELECTED_WORKER=${worker_names[$model_idx]}

# 3. 选择 backend_type
echo "--------------------------------"
echo "请选择新的 backend_type (输入数字):"
options=("vllm" "ftransformers" "vox-box")
PS3="选择编号: "
select opt in "${options[@]}"; do
    case $opt in
        "vllm"|"ftransformers"|"vox-box")
            NEW_BACKEND=$opt
            break
            ;;
        *) echo "无效选项，请重新输入数字选择。";;
    esac
done

# 4. 判断并处理 worker_name
UPDATE_WORKER_SQL=""
if [[ -z "$SELECTED_WORKER" || "$SELECTED_WORKER" == "NULL" ]]; then
    echo "--------------------------------"
    echo "提示：检测到 worker_name 为空，将自动补全为 'worker qujing 2'"
    UPDATE_WORKER_SQL=", worker_name='worker qujing 2'"
else
    echo "--------------------------------"
    echo "检测到已有 worker_name ($SELECTED_WORKER)，将保持现状。"
fi

# 5. 执行数据库更新
SQL_EXEC="UPDATE model_store SET backend_type='$NEW_BACKEND' $UPDATE_WORKER_SQL WHERE name='$SELECTED_MODEL' AND source='upload';"

echo "正在执行数据库更新..."
mysql_exec "-e \"$SQL_EXEC\""

if [ $? -eq 0 ]; then
    echo "--------------------------------"
    echo "成功：模型 '$SELECTED_MODEL' 已成功配置！"
    echo "Backend: $NEW_BACKEND"
    [ -n "$UPDATE_WORKER_SQL" ] && echo "Worker : worker qujing 2"
else
    echo "--------------------------------"
    echo "错误：数据库更新失败，请检查容器状态。"
fi
