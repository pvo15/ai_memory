// What to say to Claude / GLM so tasks get registered and continue across chats.
// Every phrase has a Copy button — paste it straight into the chat.
import { BookOpen, Brain, Check, Code2, Copy, Flag, GitBranch, Lightbulb, MessageSquareText, Moon, Play, Repeat, Ticket } from 'lucide-react';
import { useState, type ComponentType, type ReactNode } from 'react';

import { Card, useToast } from '../components/ui';

interface Phrase { say: string; does: string; tool?: string }
interface Section { title: string; icon: ComponentType<{ size?: number }>; when: string; phrases: Phrase[]; note?: ReactNode }

const SECTIONS: Section[] = [
    {
        title: '1 · Start a task',
        icon: Play,
        when: 'Beginning work on a ticket',
        note: <>Easiest: <b>create the branch with the ticket key</b> (e.g. <code>feature-PROJ-142</code>) and just open a chat — the journal is created automatically with the Jira story. Use these phrases only when you're not on a ticket branch.</>,
        phrases: [
            { say: 'Start task PROJ-142', does: 'Creates the task journal and loads the Jira story', tool: 'task_start' },
            { say: 'Start task PROJ-142 and summarise the story and acceptance criteria', does: 'Starts it and gives you the requirements up front', tool: 'task_start + jira_story' },
            { say: 'Start task PROJ-142 and checkpoint what is already done on this branch', does: 'For a ticket you began earlier without a journal', tool: 'task_start + task_checkpoint' },
        ],
    },
    {
        title: '2 · While working',
        icon: Lightbulb,
        when: 'Record what matters as you go',
        phrases: [
            { say: 'Decision: we sort templates on the client because the API has no sort parameter', does: 'Saved forever, shown in every briefing — the "why" for later', tool: 'task_log (decision)' },
            { say: 'Log progress: the template rename flow is done and tested', does: 'Adds a step to the task history', tool: 'task_log' },
            { say: 'Blocker: waiting for the designer to confirm the empty-state text', does: 'Marks what you are waiting for', tool: 'task_log (blocker)' },
            { say: 'What is the status of this task?', does: 'Shows current state, next steps and decisions', tool: 'task_resume' },
        ],
    },
    {
        title: '3 · Switch task',
        icon: Repeat,
        when: 'Moving to another ticket (then switch the git branch)',
        phrases: [
            { say: 'Checkpoint, I am switching task', does: 'Saves "done so far + next steps" so you can come back', tool: 'task_checkpoint' },
            { say: 'Pause task PROJ-142, I will come back to it later', does: 'Checkpoint + marks it paused', tool: 'task_checkpoint + task_set_status' },
            { say: 'Resume task PROJ-150', does: 'Loads another task without switching branch', tool: 'task_resume' },
        ],
    },
    {
        title: '4 · End of day',
        icon: Moon,
        when: 'Before closing the chat or the laptop',
        note: <>The one habit that matters: <b>say “checkpoint” before you stop</b>. Tomorrow — in Claude or GLM — the chat starts exactly here.</>,
        phrases: [
            { say: 'Checkpoint', does: 'Saves where you are and the next steps', tool: 'task_checkpoint' },
            { say: 'Checkpoint with a clear list of next steps for tomorrow', does: 'Same, with a concrete to-do list', tool: 'task_checkpoint' },
        ],
    },
    {
        title: '5 · Finish a task',
        icon: Flag,
        when: 'The ticket is done (merged / ready for QA)',
        phrases: [
            { say: 'Final checkpoint for PROJ-142 and mark it done', does: 'Writes the final summary and closes the task', tool: 'task_checkpoint + task_set_status' },
            { say: 'Check the changes against the acceptance criteria of PROJ-142', does: 'Compares your work with the story before you finish', tool: 'jira_story' },
            { say: 'Reopen task PROJ-142', does: 'If QA sends it back', tool: 'task_set_status' },
        ],
    },
    {
        title: 'Business context',
        icon: Ticket,
        when: 'Jira stories and project rules',
        phrases: [
            { say: 'Show the acceptance criteria of this story', does: 'Full story from the local Jira copy', tool: 'jira_story' },
            { say: 'Find stories about FX rates', does: 'Searches all collected Jira issues', tool: 'search_stories' },
            { say: 'What are the project rules for forms?', does: 'Architecture / code-style rules by topic', tool: 'project_rules' },
        ],
    },
    {
        title: 'Code',
        icon: Code2,
        when: 'Finding and understanding code',
        phrases: [
            { say: 'Where are payment templates saved?', does: 'Search by meaning', tool: 'code_search' },
            { say: 'Open the file for PaymentTemplatesPanel', does: 'Find a file by name or path', tool: 'find_files' },
            { say: 'What is this code and which story is it from? (paste the code below)', does: 'Location + commits that wrote it + Jira story', tool: 'explain_snippet' },
            { say: 'Why do templates keep their position on rename?', does: 'Code + commits + story → the reason', tool: 'explain_code' },
            { say: 'Who changed PaymentTemplatesPanel recently and why?', does: 'File history with commit messages', tool: 'file_history' },
        ],
    },
    {
        title: 'Memory',
        icon: Brain,
        when: 'Teach every AI a preference once',
        phrases: [
            { say: 'Remember: never use raw axios, always use request() from shared/api', does: 'Saved for Claude, GLM and the other tools', tool: 'remember' },
            { say: 'What do you remember about forms in this project?', does: 'Searches saved facts', tool: 'recall' },
            { say: 'That memory is outdated — update it to …', does: 'Fixes a wrong fact', tool: 'update_memory' },
        ],
    },
];

const DAY = [
    { icon: GitBranch, text: 'git checkout -b feature-PROJ-142', sub: 'branch name with the ticket key' },
    { icon: MessageSquareText, text: 'Open Claude or GLM', sub: 'journal + Jira story load automatically' },
    { icon: Lightbulb, text: '“Decision: … because …”', sub: 'for important choices' },
    { icon: Moon, text: '“Checkpoint”', sub: 'before switching or stopping' },
    { icon: Flag, text: '“Final checkpoint and mark it done”', sub: 'when the ticket is finished' },
];

/** Clipboard API first; if the browser blocks it, the classic hidden-textarea copy. */
async function copyText(text: string): Promise<boolean> {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
        document.body.appendChild(ta);
        ta.select();
        let ok = false;
        try {
            ok = document.execCommand('copy');
        } catch {
            ok = false;
        }
        ta.remove();
        return ok;
    }
}

export function CopyButton({ text }: { text: string }) {
    const [done, setDone] = useState(false);
    const toast = useToast();
    return (
        <button
            className="btn small"
            onClick={async () => {
                if (await copyText(text)) {
                    setDone(true);
                    toast('Copied — paste it into Claude or GLM');
                    setTimeout(() => setDone(false), 1500);
                } else {
                    toast('Copy blocked by the browser — select the text instead');
                }
            }}
            aria-label={`Copy: ${text}`}
        >
            {done ? <Check size={13} /> : <Copy size={13} />} {done ? 'Copied' : 'Copy'}
        </button>
    );
}

export function CheatsheetPage() {
    return (
        <div className="page">
            <Card title="The whole workflow in five steps" icon={<BookOpen size={14} />}>
                <ol className="day-flow">
                    {DAY.map(({ icon: Icon, text, sub }) => (
                        <li key={text}>
                            <Icon size={16} aria-hidden />
                            <div><b className="mono">{text}</b><div className="muted small">{sub}</div></div>
                        </li>
                    ))}
                </ol>
                <p className="muted small" style={{ marginBottom: 0 }}>
                    Put the ticket key in <b>branch names</b> and <b>commit messages</b> (<code>PROJ-142: …</code>) — that's what
                    links chats, journals, Jira stories and the “why” behind every line of code.
                </p>
            </Card>
            <div className="grid-2">
                {SECTIONS.map(({ title, icon: Icon, when, phrases, note }) => (
                    <Card key={title} title={title} icon={<Icon size={14} />}>
                        <p className="muted small" style={{ marginTop: 0 }}>{when}</p>
                        {note && <p className="cheat-note small">{note}</p>}
                        <table>
                            <tbody>
                                {phrases.map((p) => (
                                    <tr key={p.say}>
                                        <td>
                                            <div className="cheat-say">“{p.say}”</div>
                                            <div className="muted small">{p.does}{p.tool && <> · <code>{p.tool}</code></>}</div>
                                        </td>
                                        <td style={{ width: 90, textAlign: 'right' }}><CopyButton text={p.say} /></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </Card>
                ))}
            </div>
        </div>
    );
}

const QUICK: Phrase[] = [
    { say: 'Start task PROJ-142', does: 'only if you are not on a ticket branch', tool: 'task_start' },
    { say: 'Decision: … because …', does: 'important choices — kept forever', tool: 'task_log' },
    { say: 'Checkpoint', does: 'before switching task or stopping', tool: 'task_checkpoint' },
    { say: 'Final checkpoint and mark it done', does: 'ticket finished', tool: 'task_set_status' },
    { say: 'What is this code and which story is it from?', does: 'paste the code below it', tool: 'explain_snippet' },
];

/** Compact card for the Overview page: the five phrases used every day. */
export function QuickCommandsCard() {
    return (
        <Card
            title="Quick commands for Claude / GLM"
            icon={<BookOpen size={14} />}
            actions={<a className="btn small" href="#cheatsheet">Full cheat sheet →</a>}
        >
            <table>
                <tbody>
                    {QUICK.map((p) => (
                        <tr key={p.say}>
                            <td>
                                <div className="cheat-say">“{p.say}”</div>
                                <div className="muted small">{p.does} · <code>{p.tool}</code></div>
                            </td>
                            <td style={{ width: 90, textAlign: 'right' }}><CopyButton text={p.say} /></td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </Card>
    );
}
