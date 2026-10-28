import { useEffect, useRef } from "react";
import type { TaskPreparation as Preparation } from "../types";

export default function TaskPreparation({ value, answers, onAnswers, disabled }: {
  value: Preparation;
  answers: Record<string, string>;
  onAnswers: (answers: Record<string, string>) => void;
  disabled: boolean;
}) {
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    const index = value.questions.findIndex(question => !value.answers[question.id]);
    section.current?.querySelectorAll("fieldset")[Math.max(0, index)]?.querySelector<HTMLInputElement>("input")?.focus();
  }, [value]);
  return <section ref={section} className="task-preparation" aria-label="任务澄清">
    <header><h3>先明确{value.questions.length > 1 ? "两点信息" : "一件事"}</h3><span>本步免费，不调用模型</span></header>
    {value.questions.map(question => <fieldset key={question.id} disabled={disabled}>
      <legend>{question.prompt}</legend>
      {question.options.length > 0 && <div className="preparation-options">
        {question.options.map(option => <label key={option}>
          <input type="radio" name={`clarify-${question.id}`} value={option}
            checked={answers[question.id] === option}
            onChange={() => onAnswers({ ...answers, [question.id]: option })} />{option}
        </label>)}
      </div>}
      <input type={question.id === "file_url" ? "url" : "text"}
        aria-label={question.options.length ? `${question.prompt}（自定义）` : question.prompt}
        placeholder={question.options.length ? "也可以直接填写你的要求" : question.id === "file_url" ? "https://…" : "例如：比较 Notion 和飞书的免费版"}
        maxLength={400}
        value={question.options.includes(answers[question.id]) ? "" : answers[question.id] ?? ""}
        onChange={e => onAnswers({ ...answers, [question.id]: e.target.value })} />
    </fieldset>)}
    <details><summary>默认如何完成</summary>
      <p>{value.scope}</p><ul>{value.defaults.map(item => <li key={item}>{item}</li>)}</ul>
    </details>
  </section>;
}
