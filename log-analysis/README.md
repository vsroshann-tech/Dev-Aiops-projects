# 🔍 AIOps Log Analysis & ML Anomaly Detection Pipeline

An automated log parsing, frequency tracking, and anomaly detection utility built to bridge traditional Linux system administration with modern AIOps workflows. This pipeline ingests raw system logs, extracts structured time-series features, tracks error bursts, and utilizes unsupervised machine learning to isolate behavioral anomalies without manual inspection.

---

## 📂 Project Structure

```text
log-analysis/
├── README.md                  # Comprehensive project documentation & results
├── aiops_log_analysis.py      # ML-based anomaly detection script (Isolation Forest)
├── simple_log_analysis.py     # Rule-based time-window error frequency detector
└── system_logs.txt            # Raw system log dataset
```

---

## 🛠️ Tech Stack & Libraries

* **Language:** Python 3.14
* **Data Manipulation & Parsing:** Pandas, NumPy, Regular Expressions (`re`)
* **Machine Learning:** Scikit-learn (`IsolationForest`)
* **Environment:** Ubuntu Linux, Python Virtual Environments (`venv`)

---

## ⚙️ How It Works

1. **Regex Parsing:** Ingests unstructured system logs (`system_logs.txt`), extracting timestamps, log levels (`INFO`, `WARNING`, `ERROR`, `CRITICAL`), and raw event messages into a structured Pandas DataFrame.
2. **Rule-Based Spike Detection (`simple_log_analysis.py`):** Converts timestamps to datetime objects, bins errors into 30-second intervals, and flags operational error bursts exceeding safety thresholds.
3. **Machine Learning Anomaly Detection (`aiops_log_analysis.py`):** 
   * Maps qualitative log severities to numeric weights (`INFO: 1` to `CRITICAL: 4`).
   * Computes quantitative message lengths.
   * Feeds engineered feature vectors into an **Isolation Forest** model (`contamination=0.1`) to autonomously isolate abnormal log signatures.

---

## 📊 Results & Sample Output

### 1. Rule-Based Frequency & Spike Detection
Running `simple_log_analysis.py` successfully flags time-window error bursts exceeding the safety threshold:

```bash
(loganalysis) roshan@DESKTOP-RV30KSR:~/ai-assisted-devops/log-analysis$ python3 simple_log_analysis.py
🚨 Anomaly detected! 4 ERROR logs in 30 seconds at 2025-03-27 10:02:30
🚨 Anomaly detected! 4 ERROR logs in 30 seconds at 2025-03-27 10:11:00

Full Log Analysis:
              timestamp     level                                     message
0   2025-03-27 10:00:00      INFO                Suspicious IP access blocked
1   2025-03-27 10:00:04      INFO                          CPU usage at 95%
...
997 2025-03-27 11:06:28  CRITICAL                          CPU usage at 95%
[1000 rows x 3 columns]
```

### 2. Machine Learning Anomaly Detection (Isolation Forest)
Running `aiops_log_analysis.py` applies unsupervised ML to detect behavioral anomalies across severity scores and message structures:

```bash
(loganalysis) roshan@DESKTOP-RV30KSR:~/ai-assisted-devops/log-analysis$ python3 aiops_log_analysis.py

🔍 **Detected Anomalies:**
                timestamp     level                                       message  level_score  message_length  anomaly is_anomaly
19  2025-03-27 10:00:38   WARNING                                User logged in            2              14       -1     ❌ Anomaly
21  2025-03-27 10:01:45  CRITICAL                         User changed password            4              21       -1     ❌ Anomaly
23  2025-03-27 10:01:09  CRITICAL          Transaction rollback due to deadlock            4              36       -1     ❌ Anomaly
24  2025-03-27 10:02:00   WARNING       Slow query detected: SELECT * FROM orders            2              41       -1     ❌ Anomaly
26  2025-03-27 10:01:44     ERROR                            CPU usage at 95%            3              16       -1     ❌ Anomaly
...
887 2025-03-27 10:14:47   WARNING       Slow query detected: SELECT * FROM orders            2              41       -1     ❌ Anomaly
941 2025-03-27 11:02:44   WARNING      Unauthorized access attempt to admin panel            2              42       -1     ❌ Anomaly
942 2025-03-27 10:47:06   WARNING  Multiple failed login attempts, account locked            2              46       -1     ❌ Anomaly
955 2025-03-27 10:47:45  CRITICAL                          User session expired            4              20       -1     ❌ Anomaly
997 2025-03-27 11:06:28  CRITICAL                            CPU usage at 95%            4              16       -1     ❌ Anomaly

[85 rows x 7 columns]
```

---

## 🚀 Quickstart & Local Execution

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/ai-assisted-devops.git
cd ai-assisted-devops/log-analysis

# Create and activate virtual environment
python3 -m venv loganalysis
source loganalysis/bin/activate

# Install dependencies
pip install pandas numpy scikit-learn

# Run the analysis scripts
python3 simple_log_analysis.py
python3 aiops_log_analysis.py
```

---
*Maintained by Roshan — DevOps Engineer
