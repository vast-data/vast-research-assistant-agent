import { Injectable, inject, signal, computed, effect } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { SETTINGS } from '../settings';

// v2: v1 persisted built-in mode prompts as if user-edited, which pinned them forever.
const STORAGE_KEY = 'vastrag_prompt_state_v2';

type PromptMode = 'none' | 'deep' | 'gsf';

interface PromptPersistence {
  userPrompt: string | null;
}

@Injectable({ providedIn: 'root' })
export class SystemPromptService {
  private http = inject(HttpClient);

  defaultSystemPrompt = signal('');
  deepResearchPrompt = signal('');
  gsfPrompt = signal('');
  gsfDeepResearchPrompt = signal('');
  loaded = signal(false);

  private researchMode = signal<PromptMode>('none');
  /** Prompt saved by the user in the System Prompt dialog; null means use the built-in one. */
  private userPrompt = signal<string | null>(null);

  /** Built-in prompt for the active research mode. */
  builtInPrompt = computed(() => {
    switch (this.researchMode()) {
      case 'gsf':
        return this.gsfPrompt() || this.defaultSystemPrompt();
      case 'deep':
        return this.deepResearchPrompt() || this.defaultSystemPrompt();
      default:
        return this.defaultSystemPrompt();
    }
  });

  /** Prompt currently in effect (shown and edited in the System Prompt dialog). */
  customSystemPrompt = computed(() => this.userPrompt() ?? this.builtInPrompt());

  hasCustomPrompt = computed(() => this.userPrompt() !== null);

  constructor() {
    effect(() => {
      if (!this.loaded()) return;
      const state: PromptPersistence = { userPrompt: this.userPrompt() };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    });
  }

  loadSystemPrompt(): void {
    if (this.loaded()) return;
    this.http.get<{
      system_prompt: string;
      deep_research_prompt?: string;
      gsf_prompt?: string;
      gsf_deep_research_prompt?: string;
    }>(`${SETTINGS.BASE_API_URL}/system-prompt`).subscribe({
      next: (response) => {
        this.defaultSystemPrompt.set(response.system_prompt);
        this.deepResearchPrompt.set(response.deep_research_prompt ?? '');
        this.gsfPrompt.set(response.gsf_prompt ?? '');
        this.gsfDeepResearchPrompt.set(response.gsf_deep_research_prompt ?? '');
        this.userPrompt.set(this.loadFromStorage()?.userPrompt ?? null);
        this.loaded.set(true);
      },
      error: (err) => console.error('Failed to load system prompt:', err)
    });
  }

  private loadFromStorage(): PromptPersistence | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  applyResearchMode(mode: 'none' | 'internet' | 'deep' | 'gsf'): void {
    this.researchMode.set(mode === 'internet' ? 'none' : mode);
  }

  savePrompt(prompt: string): void {
    this.userPrompt.set(prompt === this.builtInPrompt() ? null : prompt);
  }

  resetPrompt(): void {
    this.userPrompt.set(null);
  }

  /**
   * System prompt to send with the next request, or null to let the backend use its default.
   * A user-saved prompt always wins; otherwise GSF and deep research send their built-in prompt.
   */
  getEffectivePrompt(gsfEnabled = false, deepEnabled = false): string | null {
    const user = this.userPrompt();
    if (user !== null) {
      return user || null;
    }
    if (gsfEnabled) {
      return this.gsfPrompt() || this.defaultSystemPrompt() || null;
    }
    if (deepEnabled) {
      return this.deepResearchPrompt() || null;
    }
    return null;
  }
}
