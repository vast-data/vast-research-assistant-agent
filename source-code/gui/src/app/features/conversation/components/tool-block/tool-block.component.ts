import {
  ChangeDetectionStrategy,
  Component,
  Input,
  inject,
  signal
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIcon } from '@angular/material/icon';
import { ToolContentBlock } from '../../../reports/services/session.mapper';
import { formatDuration } from '../../../reports/utils/stream-event.utils';
import { FeatureSettingsService } from '../../../shared/services/feature-settings.service';

@Component({
  selector: 'app-tool-block',
  standalone: true,
  imports: [CommonModule, MatIcon],
  templateUrl: './tool-block.component.html',
  styleUrl: './tool-block.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class ToolBlockComponent {
  @Input({ required: true }) tool!: ToolContentBlock;

  private featureSettings = inject(FeatureSettingsService);

  expanded = signal(false);

  toggle(): void {
    this.expanded.update(v => !v);
  }

  get displayName(): string {
    return this.formatToolName(this.tool.toolName);
  }

  get isRagTool(): boolean {
    return this.tool.toolName === 'retrieve_chunks';
  }

  get modelLabel(): string {
    if (this.isRagTool) {
      return `Embedding · ${this.featureSettings.embeddingModel()}`;
    }
    return `Reasoning · ${this.featureSettings.modelId()}`;
  }

  get modelLabelFull(): string {
    const provider = this.featureSettings.provider();
    if (this.isRagTool) {
      return `Embedding model: ${this.featureSettings.embeddingModel()} (${provider})`;
    }
    return `Reasoning model: ${this.featureSettings.modelId()} (${provider})`;
  }

  get durationText(): string {
    if (this.tool.status === 'loading') {
      return 'Running...';
    }
    return this.tool.duration ? formatDuration(this.tool.duration) : '';
  }

  get citationsCount(): number {
    return this.tool.citations?.length ?? 0;
  }

  get chunksCount(): number {
    return this.tool.chunks?.length ?? 0;
  }

  get hasExpandableContent(): boolean {
    return !!this.tool.prompt || !!this.queryText || !!this.metadataQueryText || !!this.collectionsText || this.citationsCount > 0 || this.chunksCount > 0;
  }

  get queryText(): string | null {
    // Get prompt or query from arguments
    const args = this.tool.arguments;
    if (!args) return null;
    return (args['prompt'] as string) || (args['query'] as string) || (args['similarity_query'] as string) || null;
  }

  get collectionsText(): string | null {
    const args = this.tool.arguments;
    const raw = args?.['collection_names'] ?? args?.['collection_name'];
    if (raw == null || raw === '') return null;
    if (typeof raw === 'string') return raw;
    if (Array.isArray(raw)) {
      const parts = raw.map(String).filter(Boolean);
      return parts.length ? parts.join('\n') : null;
    }
    return null;
  }

  get metadataQueryText(): string | null {
    const args = this.tool.arguments;
    const fromArgs = this.formatMetadataFilters(args?.['metadata_filters']);
    if (fromArgs) return fromArgs;
    if (this.tool.metadataQueryUsed) return this.tool.metadataQueryUsed;
    return null;
  }

  private formatMetadataFilters(raw: unknown): string | null {
    if (raw == null || raw === '') return null;
    if (typeof raw === 'string') {
      const s = raw.trim();
      return s || null;
    }
    if (Array.isArray(raw)) {
      const parts = raw
        .map(item => {
          if (typeof item === 'string') return item.trim();
          if (item && typeof item === 'object') {
            const obj = item as Record<string, unknown>;
            const key = obj['key'] ?? obj['field'];
            const op = obj['op'] ?? obj['operator'] ?? 'eq';
            const value = obj['value'];
            if (key != null && value != null) return `${key}:${op}:${value}`;
          }
          return '';
        })
        .filter(Boolean);
      return parts.length ? parts.join('\n') : null;
    }
    return null;
  }

  get isWarning(): boolean {
    return this.tool.status === 'warning';
  }

  get errorMessage(): string | null {
    return this.tool.error ?? null;
  }

  private formatToolName(name: string): string {
    if (name === 'retrieve_chunks') {
      return 'RAG Results';
    }
    if (name === 'think') {
      return 'Thinking';
    }
    return name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, char => char.toUpperCase());
  }

  /** Build a map from source-title → source-url using chunks data */
  get citationUrlMap(): Record<string, string> {
    const map: Record<string, string> = {};
    if (this.tool.chunks) {
      for (const chunk of this.tool.chunks) {
        if (chunk.sourceTitle && chunk.sourceUrl && !map[chunk.sourceTitle]) {
          map[chunk.sourceTitle] = chunk.sourceUrl;
        }
      }
    }
    return map;
  }

  getCitationUrl(citation: string): string | null {
    return this.citationUrlMap[citation] ?? null;
  }

  truncateText(text: string, maxLength = 200): string {
    if (text.length <= maxLength) return text;
    return text.slice(0, maxLength) + '...';
  }
}

