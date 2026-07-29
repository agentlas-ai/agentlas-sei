from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .util import read_json, unique_id, utc_now


@dataclass(frozen=True)
class InterviewQuestion:
    question_id: str
    topic: str
    prompt: str
    required: bool = True


QUESTIONS = (
    InterviewQuestion(
        "product-purpose",
        "intent",
        "이 프로젝트가 사용자에게 제공해야 하는 가장 중요한 결과는 무엇입니까?",
    ),
    InterviewQuestion(
        "critical-journeys",
        "flow",
        "절대 깨지면 안 되는 핵심 사용자 흐름을 최대 3개 적어주세요. 쉼표로 구분할 수 있습니다.",
    ),
    InterviewQuestion(
        "success-oracle",
        "outcome",
        "로그나 API 성공이 아니라, 사용자가 성공했다고 판단할 수 있는 최종 상태는 무엇입니까?",
    ),
    InterviewQuestion(
        "forbidden-states",
        "risk",
        "절대 발생하면 안 되는 상태나 부작용은 무엇입니까?",
    ),
    InterviewQuestion(
        "fallback-policy",
        "degradation",
        "허용되는 fallback과 허용되지 않는 fallback은 각각 무엇입니까?",
    ),
    InterviewQuestion(
        "fragile-areas",
        "belief",
        "팀이 불안해하거나 담당자 없이 오래 방치한 영역은 어디입니까?",
    ),
    InterviewQuestion(
        "temporary-decisions",
        "intent-debt",
        "임시로 만들었지만 아직 제거하거나 재검토하지 못한 결정은 무엇입니까?",
    ),
    InterviewQuestion(
        "evidence-boundary",
        "privacy",
        "분석 과정에서 외부 전송하거나 장기 저장하면 안 되는 데이터는 무엇입니까?",
    ),
)


def question_template() -> dict[str, Any]:
    return {
        "schemaVersion": "sei.interview-answers.v1",
        "answers": {question.question_id: "" for question in QUESTIONS},
    }


def load_answers(path: Path) -> dict[str, str]:
    value = read_json(path)
    if value.get("schemaVersion") != "sei.interview-answers.v1":
        raise ConfigurationError("Unsupported interview answer schema.")
    answers = value.get("answers")
    if not isinstance(answers, dict):
        raise ConfigurationError("Interview answers must be an object.")
    normalized: dict[str, str] = {}
    for question in QUESTIONS:
        answer = answers.get(question.question_id, "")
        if not isinstance(answer, str):
            raise ConfigurationError(
                f"Interview answer must be text: {question.question_id}"
            )
        answer = answer.strip()
        if question.required and not answer:
            raise ConfigurationError(
                f"Missing required interview answer: {question.question_id}"
            )
        normalized[question.question_id] = answer
    for key, answer in answers.items():
        if not re.fullmatch(r"flow-\d+-(?:meso|micro-\d+)", str(key)):
            continue
        if not isinstance(answer, str):
            raise ConfigurationError(f"Interview answer must be text: {key}")
        if answer.strip():
            normalized[str(key)] = answer.strip()
    return normalized


def run_interactive(
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> dict[str, str]:
    output_fn(
        "SEI 내장 인터뷰어입니다. 답변은 사실이 아니라 belief/intent 후보로 저장됩니다."
    )
    answers: dict[str, str] = {}
    for index, question in enumerate(QUESTIONS, 1):
        output_fn(f"\n[{index}/{len(QUESTIONS)}] {question.prompt}")
        while True:
            value = input_fn("> ").strip()
            if value or not question.required:
                answers[question.question_id] = value
                break
            output_fn("필수 질문입니다. 모르면 '모름'이라고 입력해도 됩니다.")
    journeys = [
        item.strip()
        for item in re.split(
            r"\s*(?:,|;|\n|\r|→|->)\s*", answers["critical-journeys"]
        )
        if item.strip()
    ][:3]
    output_fn(
        "\n이제 각 큰 사용자 흐름을 실제 과정으로 나눕니다. "
        "단계 사이는 `>`로 구분해 주세요."
    )
    for journey_index, journey in enumerate(journeys, 1):
        output_fn(
            f"\n[흐름 {journey_index}] “{journey}”가 끝날 때까지 거치는 "
            "중간 과정을 2~5개 적어주세요."
        )
        meso_answer = input_fn("> ").strip() or journey
        answers[f"flow-{journey_index}-meso"] = meso_answer
        meso_steps = [
            item.strip()
            for item in re.split(r"\s*(?:->|→|>)\s*", meso_answer)
            if item.strip()
        ][:5]
        for meso_index, meso in enumerate(meso_steps, 1):
            output_fn(
                f"  “{meso}”에서 실제로 일어나는 작은 단계를 "
                "1~5개 적어주세요."
            )
            micro_answer = input_fn("> ").strip() or meso
            answers[f"flow-{journey_index}-micro-{meso_index}"] = micro_answer
    return answers


def build_interview_record(
    project_name: str,
    answers: dict[str, str],
    accepted_by: str | None,
) -> dict[str, Any]:
    created_at = utc_now()
    payload = {
        "project": project_name,
        "answers": answers,
        "createdAt": created_at,
    }
    return {
        "schemaVersion": "sei.interview.v1",
        "interviewId": unique_id("interview", payload),
        "project": project_name,
        "answers": answers,
        "epistemicStatus": "accepted-intent" if accepted_by else "belief",
        "acceptedBy": accepted_by,
        "createdAt": created_at,
        "limitations": [
            "Interview answers are human statements, not runtime proof.",
            "Unverified answers must remain belief or intent candidates.",
        ],
    }
