#!/bin/bash

# --- 配置参数 ---
CONTAINER_NAME="zhiwen-mysql"
DB_USER="root"
DB_PASS="zhiwen_password"
DB_NAME="ames"

# 1. 查询私有化导入的模型 (source='upload')
echo "正在从数据库查询私有化导入的模型..."
MODELS_DATA=$(sudo docker exec -i $CONTAINER_NAME mysql -u$DB_USER -p$DB_PASS $DB_NAME -N -e "SELECT name, worker_name FROM model_store WHERE source='upload';")

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
echo "请选择要修改的模型编号:"
for i in "${!model_names[@]}"; do
    printf "[%d] 模型名: %-25s | 当前 Worker: %s\n" "$i" "${model_names[$i]}" "${worker_names[$i]:-NULL}"
done

read -p "输入编号: " model_idx

# 校验输入
if [[ ! $model_idx =~ ^[0-9]+$ ]] || [ $model_idx -ge ${#model_names[@]} ]; then
    echo "无效的选择，程序结束。"
    exit 1
fi

SELECTED_MODEL=${model_names[$model_idx]}
SELECTED_WORKER=${worker_names[$model_idx]}

# 3. 选择 backend_type
echo "--------------------------------"
echo "请选择新的 backend_type:"
options=("vllm" "ftransformers" "vox-box")
select opt in "${options[@]}"; do
    case $opt in
        "vllm"|"ftransformers"|"vox-box")
            NEW_BACKEND=$opt
            break
            ;;
        *) echo "无效选项，请重新选择。";;
    esac
done

# 4. 判断并处理 worker_name
UPDATE_WORKER_SQL=""
if [[ -z "$SELECTED_WORKER" || "$SELECTED_WORKER" == "NULL" ]]; then
    echo "提示：检测到 worker_name 为空，将自动设置为 'worker qujing 2'"
    UPDATE_WORKER_SQL=", worker_name='worker qujing 2'"
else
    echo "检测到已有 worker_name ($SELECTED_WORKER)，跳过更新该字段。"
fi

# 5. 执行数据库更新
SQL_EXEC="UPDATE model_store SET backend_type='$NEW_BACKEND' $UPDATE_WORKER_SQL WHERE name='$SELECTED_MODEL' AND source='upload';"

echo "正在执行更新..."
sudo docker exec -i $CONTAINER_NAME mysql -u$DB_USER -p$DB_PASS $DB_NAME -e "$SQL_EXEC"

if [ $? -eq 0 ]; then
    echo "成功：模型 '$SELECTED_MODEL' 已更新为 $NEW_BACKEND。"
else
    echo "错误：更新失败。"
fi
