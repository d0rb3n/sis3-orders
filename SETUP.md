# Practice 7 — GitHub Actions + OIDC → SIS 3 (ECS через CloudFormation)

Регион: eu-north-1 (как в deploy.sh). Заменяй <ACCOUNT_ID>, <OWNER>, <REPO>, <STACK_PREFIX>.

## 0. Что менять в репозитории
Шаблоны infra/*.yaml и код НЕ меняются: в app.yaml уже есть ImageTag, запрет latest,
DeploymentCircuitBreaker с Rollback, в ecr.yaml — IMMUTABLE и ScanOnPush.
Добавить в репозиторий:
- .github/workflows/deploy.yml
- (для отчёта) iam/trust-policy.json и iam/deploy-permissions.json
Репозиторий должен быть на GitHub, ветка main.

## 1. OIDC provider (один раз на аккаунт)
Проверить: `aws iam list-open-id-connect-providers`
Если нет token.actions.githubusercontent.com:
```
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```
(thumbprint не нужен.)

## 2. Узнать реальный sub (самая частая ошибка!)
```
gh api repos/<OWNER>/<REPO>/actions/oidc/customization/sub
```
Если репозиторий создан после 15.07.2026, будет формат repo:<OWNER>@<ID>/<REPO>@<ID>.
В trust-policy.json подставь полученный префикс + `:environment:production`.

## 3. Создать роль
```
aws iam create-role --role-name gha-deploy-sis3 \
  --assume-role-policy-document file://iam/trust-policy.json
aws iam put-role-policy --role-name gha-deploy-sis3 \
  --policy-name deploy --policy-document file://iam/deploy-permissions.json
```
Подставь <ACCOUNT_ID>, <REGION>=eu-north-1, <STACK_PREFIX> (например sis3-dev) в обоих json.
ARN роли: arn:aws:iam::<ACCOUNT_ID>:role/gha-deploy-sis3

## 4. GitHub: Settings → Environments → New environment: `production`
(по желанию Required reviewers). Затем в этом environment (или в Variables репозитория):
| Variable         | Значение                               |
|------------------|----------------------------------------|
| DEPLOY_ROLE_ARN  | arn:aws:iam::<ACCOUNT_ID>:role/gha-deploy-sis3 |
| STACK_PREFIX     | тот же префикс, что в deploy.sh (напр. sis3-dev) |
| APP_ENV          | dev (или defence/prod — допустимые значения ecr.yaml) |
Секретов (ключей) НЕ создавать.

## 5. Запуск
```
git add .github iam && git commit -m "Add CI/CD pipeline with OIDC" && git push origin main
```
Если стек уже развёрнут через deploy.sh с тем же префиксом — пайплайн обновит его
(новый тег = новый SHA → новая ревизия task definition). Если стека нет — создаст с нуля.

## 6. Доказательства для сдачи (2 балла)
1. Ссылка на репозиторий.
2. Ссылка/скриншот ЗЕЛЁНОГО run, запущенного push-ом (вкладка Actions; в Summary виден блок Deployment evidence).
3. Новая ревизия task definition с тегом = SHA:
   ```
   aws ecs describe-task-definition --task-definition <family> \
     --query 'taskDefinition.[revision,containerDefinitions[0].image]'
   ```
   и `git rev-parse HEAD` — SHA должен совпасть. Скриншот ECS → Task definitions → последняя ревизия.
4. Trust policy (iam/trust-policy.json, account ID можно замазать).
Желательно: второй push (мелкое изменение) → ещё один зелёный run и ревизия N+1.

## Если что-то падает (слайд 22)
- Could not load credentials → нет id-token: write.
- Not authorized to perform sts:AssumeRoleWithWebIdentity → sub не совпал
  (формат с ID, отсутствует environment: production, не тот репозиторий/ветка).
- Incorrect token audience → aud должен быть ровно sts.amazonaws.com.
- AccessDenied после «Assuming role … success» → не хватает прав в deploy-permissions.json
  (смотри, какое действие названо в ошибке, добавь его).
