import { Injectable, effect, signal } from '@angular/core';

/** Mutually exclusive feature modes used by chat / prompt streaming. */
export type FeatureMode = 'none' | 'internet' | 'deep' | 'gsf' | 'aidr';

export const FEATURE_MODE_LABELS: Record<FeatureMode, string> = {
  none: 'None',
  internet: 'Internet Search',
  deep: 'Deep Research',
  gsf: 'NVIDIA Auto-Ontology',
  aidr: 'AIDR',
};

export interface ModelInfo {
  provider?: string;
  model_id?: string;
  embedding_model?: string;
}

const STORAGE_KEY = 'vastrag_feature_settings';

interface PersistedState {
  featureMode?: FeatureMode;
}

@Injectable({ providedIn: 'root' })
export class FeatureSettingsService {
  readonly featureMode = signal<FeatureMode>('none');
  /** Read-only, reported by the backend /model-info endpoint. */
  readonly provider = signal('');
  readonly modelId = signal('');
  readonly embeddingModel = signal('');

  constructor() {
    this.restore();
    effect(() => {
      const state: PersistedState = { featureMode: this.featureMode() };
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      } catch {
        /* ignore quota / private mode */
      }
    });
  }

  private restore(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as PersistedState;
      if (parsed.featureMode) this.featureMode.set(parsed.featureMode);
    } catch {
      /* ignore */
    }
  }

  /** Clicking an active mode turns it off; otherwise switches exclusively. */
  setFeatureMode(mode: FeatureMode): void {
    this.featureMode.set(this.featureMode() === mode ? 'none' : mode);
  }

  isInternetSearchEnabled = () => this.featureMode() === 'internet';
  isDeepResearchEnabled = () => this.featureMode() === 'deep';
  isGsfEnabled = () => this.featureMode() === 'gsf';
  isAidrEnabled = () => this.featureMode() === 'aidr';

  setModelInfo(info: ModelInfo): void {
    this.provider.set(info.provider ?? '');
    this.modelId.set(info.model_id ?? '');
    this.embeddingModel.set(info.embedding_model ?? '');
  }
}
