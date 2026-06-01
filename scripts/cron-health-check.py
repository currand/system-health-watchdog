#!/usr/bin/env python3
"""
Cron Health Check - runs as a probe command.
Outputs PASS if all jobs healthy, FAIL with details if any job is failing.

Usage: python3 cron-health-check.py
  Exit code 0 = healthy (PASS)
  Exit code 1 = failure(s) detected (FAIL)
"""

import json
import subprocess
import sys

def check_cron_health():
    """Check if any cron jobs are failing."""
    try:
        result = subprocess.run(
            ['hermes', 'cron', 'list'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        output = result.stdout + result.stderr
        
        # Check for failure indicators in output
        # Look for: "error:", "Error:", delivery errors, non-ok status
        failed_jobs = []
        
        # Parse human-readable output
        lines = output.split('\n')
        current_job = None
        
        for line in lines:
            if line.strip().startswith('Name:'):
                current_job = line.split(':', 1)[1].strip()
            if current_job and ('error' in line.lower() or 'fail' in line.lower()):
                if 'error:' in line.lower() or 'exit code' in line.lower():
                    failed_jobs.append(f"{current_job}: {line.strip()}")
        
        # Try JSON output if available
        try:
            result_json = subprocess.run(
                ['hermes', 'cron', 'list', '--json'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result_json.returncode == 0:
                data = json.loads(result_json.stdout)
                jobs = data if isinstance(data, list) else data.get('jobs', [])
                
                for job in jobs:
                    last_status = job.get('last_status', '')
                    last_error = job.get('last_delivery_error')
                    
                    if last_status and last_status != 'ok':
                        failed_jobs.append(f"{job.get('name', 'unknown')}: status={last_status}")
                    if last_error:
                        failed_jobs.append(f"{job.get('name', 'unknown')}: delivery_error={last_error}")
        except (json.JSONDecodeError, Exception):
            pass  # Fall back to human-readable parsing
        
        if failed_jobs:
            print("FAIL - Failed jobs:")
            for j in failed_jobs:
                print(f"  - {j}")
            return False
        else:
            print("PASS")
            return True
            
    except subprocess.TimeoutExpired:
        print("FAIL - timeout checking cron")
        return False
    except Exception as e:
        print(f"FAIL - {e}")
        return False

if __name__ == "__main__":
    success = check_cron_health()
    sys.exit(0 if success else 1)
