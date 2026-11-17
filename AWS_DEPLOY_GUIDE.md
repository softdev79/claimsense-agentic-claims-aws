# ClaimSense on AWS: step-by-step guide for first-time AWS users

This guide takes ClaimSense from your Windows laptop (PyCharm) to a live URL on AWS.
Follow it top to bottom, ticking each box. Budget about **2 hours** the first time.

```
Your laptop (PyCharm)                               AWS  (region ap-south-1, Mumbai)
─────────────────────                               ─────────────────────────────────────────────
code ──docker build──► image ──docker push──►  ECR (image store)
                                                     │
deploy.ps1 ──Terraform──────────────────────►  ECS Fargate task (1 vCPU, 2 GB, public IP)
                                                 ├─ container "api" :8000  FastAPI + LangGraph agent ──► Amazon Bedrock (Qwen3-VL)
                                                 └─ container "ui"  :8501  Streamlit portal
Bedrock key ────────────────────────────────►  SSM Parameter Store (encrypted) → injected at start-up
                                               CloudWatch Logs  ·  Security group (only your IP)  ·  AWS Budget alert
```

## What it costs (read this first)

| Item | Approx. cost | When you pay |
|---|---|---|
| ECS Fargate task (1 vCPU + 2 GB) | ~ $0.05 / hour (~ $1.20 / day if left on) | only while running |
| Public IPv4 address | ~ $0.005 / hour | only while running |
| ECR image storage | a few cents / month | until you destroy |
| CloudWatch logs, SSM parameter, Budget | ~ free at this size | – |
| Amazon Bedrock tokens | about ₹0.27 (US$0.003) per claim processed | every claim |

Prices are approximate. Check the AWS pricing pages for current numbers.

> **New AWS accounts** get a **Free plan** with sign-up credits (USD 100, plus up to USD 100 more for
> completing starter activities), valid for 6 months. This demo uses the credits; it is *not* in the
> "always free" list. Running it **only when you demo it** (`pause.ps1 -Action stop`) keeps you
> far inside the credits. If AWS blocks a service on the Free plan, upgrade to the Paid plan in
> **Billing → Account plan**. Your remaining credits carry over.

**Golden rule:** when you are not showing the project, **pause it**. When you are finished with it, **destroy it** (Part 8).

---

## Part 1: Create and secure your AWS account (one time, ~30 min)

- [ ] **1.1 Sign up** at <https://aws.amazon.com/free> → *Create an AWS account*. You need an email address,
      a phone number and a debit/credit card (India: a card with international transactions enabled;
      AWS may charge a refundable ₹2 to verify it). Choose the **Free plan** when asked.
- [ ] **1.2 Turn on MFA for the root user.** Sign in as root → top-right account name → *Security credentials*
      → *Assign MFA device* → use Google Authenticator or Microsoft Authenticator on your phone.
      The root user can do *anything* (including closing the account), so protect it and then stop using it.
- [ ] **1.3 Create your everyday admin user.**
      Console search bar → **IAM** → *Users* → *Create user*
      - User name: `claimsense-admin`
      - Tick **Provide user access to the AWS Management Console** → *I want to create an IAM user* → set a password
      - Permissions: *Attach policies directly* → tick **AdministratorAccess**
      - *Create user*. **Save the console sign-in URL** shown (it looks like `https://123456789012.signin.aws.amazon.com/console`).
- [ ] **1.4 Sign out of root and sign back in as `claimsense-admin`** using that URL. Add MFA for this user too (IAM → Users → claimsense-admin → Security credentials).
- [ ] **1.5 Pick the region.** Top-right region menu → **Asia Pacific (Mumbai) ap-south-1**. Everything in this guide uses Mumbai.
- [ ] **1.6 Turn on billing alerts for yourself.** Search **Billing and Cost Management** → *Free tier* to see how much credit you have.
      (The deploy script also creates a Budget that emails you, see Part 5.)

> Do **not** create "access keys" for this user. Part 4 uses `aws login`, which gives your laptop
> short-lived keys that rotate automatically. Nothing secret is stored on disk.

---

## Part 2: Check the tools on your laptop (~10 min)

Already installed on this laptop: AWS CLI 2.38, Terraform 1.7.3, Docker 29.9, Python 3.11 and 3.13 (use 3.13: the pinned numpy/pandas need 3.12+), Git.
Open **PowerShell** (or the PyCharm terminal) and check:

```powershell
aws --version          # needs 2.32 or newer (for `aws login`)
terraform -version     # needs 1.5 or newer
docker --version
py -3.13 --version     # the project needs Python 3.12 or newer
```

- [ ] **2.1 Start Docker Desktop** from the Start menu and wait until the bottom-left says **Engine running**.
      Then `docker info` should print details with no error.
- [ ] *(optional)* Your Terraform is the 32-bit build in `Downloads\terraform_1.7.3_windows_386`. It works, but if
      anything Terraform-related fails, install the 64-bit build with `winget install Hashicorp.Terraform`.

---

## Part 3: Open the project in PyCharm and run it locally (~20 min)

Make sure it works on your laptop before you deploy it.

- [ ] **3.1** PyCharm → *File → Open* → choose the `claimsense_aws_capstone` folder.
- [ ] **3.2 Create a virtual environment.** *Settings → Project → Python Interpreter → Add Interpreter → Add Local
      Interpreter → Virtualenv → New*, base interpreter **Python 3.13** (not 3.11: numpy 2.5 needs 3.12+), location `.venv`. Or, in the PyCharm terminal:
  ```powershell
  py -3.13 -m venv .venv
  .\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process Bypass  (this window only)
  pip install -r requirements.txt
  ```
- [ ] **3.3 Add your Amazon Bedrock settings.** This edition runs the agent on **Amazon Bedrock** through its
      OpenAI-compatible endpoint (the OpenAI Python SDK, pointed at AWS). Copy `.env.example` to `.env` and fill in:
      ```
      OPENAI_API_KEY=<your Bedrock API key>
      OPENAI_BASE_URL=https://bedrock-mantle.us-east-1.api.aws/v1
      OPENAI_PROJECT_ID=<your project id>
      ```
      `.env` is git-ignored and Docker-ignored, so it never leaves your laptop (on AWS the key comes from SSM).
      Check which models your key can use (about 1 minute, under 1 cent):
      ```powershell
      python -m scripts.check_bedrock_models
      ```
      Default model: `qwen.qwen3-vl-235b-a22b-instruct`. Override with `OPENAI_MODEL=...` in `.env`.
      Measured on the 20 labelled claims (`python -m evals.run_evals`): **route accuracy 100% (20/20),
      fraud recall 100%, payable amounts 100% correct, about ₹0.27 and 16 s per claim.**
- [ ] **3.4 Try the agent from the terminal:**
  ```powershell
  python -m scripts.generate_data                 # builds data/claims.db + 20 sample claim PDFs (no key needed)
  python -m scripts.process_claim CLM-1001 CLM-1014 CLM-1019
  ```
- [ ] **3.5 Run the API + portal** (two PyCharm terminal tabs, both with the venv active):
  ```powershell
  uvicorn app.api:app --port 8000                 # tab 1 → http://localhost:8000/docs
  streamlit run ui/portal.py                      # tab 2 → http://localhost:8501
  ```
- [ ] **3.6 (Recommended) Test the exact container that AWS will run:**
  ```powershell
  docker compose up --build                       # portal http://localhost:8501 , API http://localhost:8000/docs
  ```
  Press `Ctrl+C`, then run `docker compose down` when done. If this works, the AWS deploy will almost certainly work too.

---

## Part 4: Sign your laptop in to AWS (~5 min)

Your laptop currently has **old, invalid AWS keys** in `~\.aws\credentials` (`aws sts get-caller-identity`
says *InvalidClientTokenId*). Don't touch them. Instead, create a separate profile called `claimsense`:

```powershell
aws login --profile claimsense
```

- A browser opens. Sign in as **`claimsense-admin`** (Part 1.3) and approve.
- If it asks for a region, type `ap-south-1`.
- Check it worked:
  ```powershell
  aws sts get-caller-identity --profile claimsense
  ```
  You should see your 12-digit account ID and `...:user/claimsense-admin`.

Credentials refresh automatically for up to 12 hours. Next time, just run `aws login --profile claimsense` again.

---

## Part 5: Deploy to AWS (one command, ~10-15 min)

In the PyCharm terminal, **from the project root**, with Docker Desktop running:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -BudgetEmail your.email@example.com
```

(`-ExecutionPolicy Bypass` applies to this one command only. It doesn't change any Windows setting.)

What happens, step by step:

| Step | What the script does | AWS service |
|---|---|---|
| 0 | Checks you're signed in and Docker is running | STS |
| 1 | Stores your Bedrock API key (read from `.env`) encrypted; passes the endpoint URL + model to the container | SSM Parameter Store |
| 2 | Creates a private image registry | ECR |
| 3 | Builds the Docker image and uploads it (slowest step the first time) | ECR |
| 4 | Creates the cluster, task (api + ui containers), IAM role, firewall, logs and a USD 10/month budget alert | ECS Fargate, IAM, EC2 security group, CloudWatch, Budgets |
| 5 | Waits until healthy and prints your URLs | ECS |

At the end you'll see:

```
ClaimSense is live:
  Portal   : http://13.233.x.x:8501
  API docs : http://13.233.x.x:8000/docs
```

- [ ] **5.1** Check your inbox for an AWS Budgets email. Confirm the subscription if it asks.
- [ ] **5.2** By default **only your current IP address** can open the app, because every claim costs Bedrock credits.
      To share it with a recruiter for a live demo, re-run with `-OpenToWorld` and run `pause.ps1 -Action stop` afterwards.

Options: `-Region us-east-1`, `-AwsProfile myprofile`, `-BudgetUsd 20`, `-OpenToWorld`.

---

## Part 6: Test it on AWS

- [ ] Open the **Portal** URL → process a sample claim (e.g. `CLM-1001`, a clean small claim that should auto-approve).
- [ ] Try `CLM-1014` / `CLM-1019`: these should pause for a human adjuster or SIU. Approve or reject them in the portal.
- [ ] Open **API docs** (`/docs`) and call an endpoint from Swagger.
- [ ] See it in the AWS Console (region Mumbai): **ECS → Clusters → claimsense-cluster → Services → claimsense → Tasks**,
      **CloudWatch → Log groups → /ecs/claimsense**, **ECR → claimsense**.
- [ ] Watch live logs from the terminal:
  ```powershell
  aws logs tail /ecs/claimsense --follow --profile claimsense --region ap-south-1
  ```
- [ ] **Take screenshots** of the portal, the agent's decisions, the ECS console and the logs for your portfolio.

---

## Part 7: Day-to-day: pause, resume, update

```powershell
# Pause (stops Fargate and public-IP charges; keeps everything else)
powershell -ExecutionPolicy Bypass -File deploy\pause.ps1 -Action stop

# Resume (prints the NEW URL, because the public IP changes on every start)
powershell -ExecutionPolicy Bypass -File deploy\pause.ps1 -Action start

# You changed the code → rebuild, push, and roll out the new version
powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -BudgetEmail your.email@example.com
```

Note: claims you submit live inside the container, so they are reset whenever the task restarts.
The 20 sample claims are always there. (Making data persistent with EFS or RDS is a good "v2" for your portfolio.)

---

## Part 8: Delete everything (when you're done)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\destroy.ps1
```

This removes the ECS service and cluster, ECR repo and images, IAM role, security group, log group, budget and SSM key.
Then check **Billing and Cost Management → Bills** over the next day or two to confirm charges stop.

> Keep the folder `deploy\terraform\` (it contains `terraform.tfstate`, Terraform's record of what it created).
> If you delete it, Terraform can't clean up for you, and you'd have to delete the resources by hand in the console.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `running scripts is disabled on this system` | Use the `powershell -ExecutionPolicy Bypass -File ...` form shown above |
| `AWS sign-in check failed` / `ExpiredToken` | `aws login --profile claimsense` |
| `Docker check failed` / `dockerDesktopLinuxEngine` not found | Start Docker Desktop and wait for *Engine running* |
| `docker build` fails on `pip install` | Check your internet connection and run it again. Run `docker compose build` to see the full error |
| `services-stable` waits forever or task keeps stopping | `aws logs tail /ecs/claimsense --profile claimsense --region ap-south-1`. Usually a wrong or expired Bedrock key, or a missing OPENAI_BASE_URL: fix `.env` and re-run `deploy.ps1` |
| Browser can't open the URL | Your IP changed (mobile hotspot, new Wi-Fi): re-run `deploy.ps1`. Or the task was paused: `pause.ps1 -Action start` |
| `AccessDenied` / *not available on Free plan* | Make sure you're `claimsense-admin` with AdministratorAccess. If it names a Free-plan restriction, upgrade the plan in Billing |
| Terraform error about the provider or plugin | Install 64-bit Terraform: `winget install Hashicorp.Terraform`, open a new terminal, re-run |
| Using Git Bash instead of PowerShell | `bash deploy/deploy.sh` also works (export `OPENAI_API_KEY` and `AWS_PROFILE` first) |

---

## Turning this into a portfolio project

1. **Push to GitHub.** In the project folder: `git init`, then `git add .`, then `git commit -m "ClaimSense AWS capstone"`,
   then create a repo on GitHub and push. `.env` and Terraform state are already in `.gitignore`. Run `git status` before every commit to make sure no secrets are included.
2. **README:** add the architecture diagram above, screenshots, the eval results (`python -m evals.run_evals`)
   and a "what I changed" section.
3. **Make it yours (next steps that stand out to interviewers):**
   - ✅ Done: the agent runs on **Amazon Bedrock**. Next: use your *own* account's Bedrock API key, and try a cheaper model (`mistral.ministral-3-14b-instruct`) and compare with the evals.
   - Add **GitHub Actions + OIDC** for push-to-deploy (no stored AWS keys).
   - Add an **Application Load Balancer + HTTPS + a domain** so the URL stays the same after every restart.
   - Store claims in **EFS or RDS** so data survives restarts.
   - Add **Cognito** login in front of the portal.
