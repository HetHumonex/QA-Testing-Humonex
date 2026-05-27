#!/usr/bin/env python3
"""
NVIDIA Llama 3.2 90B Vision wrapper for Humonex QA screenshot analysis.
Called on every key step — not just failures.
"""

import base64
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger('humonex-qa')

_API_KEY = os.getenv('NVIDIA_API_KEY')
_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_MODEL   = "meta/llama-3.2-90b-vision-instruct"


def analyze_screenshot(screenshot_bytes, context, question):
    """
    Send a screenshot to NVIDIA Llama Vision and return a plain English answer.
    Never raises — returns an informational string on any failure.
    """
    if not _API_KEY:
        return "(AI analysis skipped: NVIDIA_API_KEY not set)"
    if not screenshot_bytes:
        return "(AI analysis skipped: no screenshot provided)"

    try:
        img_b64 = base64.b64encode(screenshot_bytes).decode()
        payload = {
            "model": _MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "You are a QA assistant reviewing screenshots of a CA accounting "
                                "web app called Humonex.\n"
                                f"Context: {context}\n\n"
                                f"Question: {question}\n\n"
                                "Be concise and specific. Only describe what you can actually see."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 250,
            "temperature": 0.1,
            "stream": False,
        }

        r = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    except Exception as e:
        logger.warning(f"AI analysis failed: {e}")
        return f"(AI analysis unavailable: {e})"