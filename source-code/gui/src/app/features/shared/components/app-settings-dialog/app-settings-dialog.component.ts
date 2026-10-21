import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatDialogRef, MatDialogModule } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';
import { FeatureMode, FeatureSettingsService } from '../../services/feature-settings.service';
import { SystemPromptService } from '../../services/system-prompt.service';

@Component({
  selector: 'app-app-settings-dialog',
  standalone: true,
  imports: [
    MatDialogModule,
    MatIconModule,
    MatButtonModule,
    MatTooltipModule,
  ],
  template: `
    <div class="settings-dialog">
      <div class="settings-dialog__header">
        <div class="settings-dialog__title">
          <mat-icon>settings</mat-icon>
          <span>Settings</span>
        </div>
        <button class="settings-dialog__close-btn" (click)="close()" matTooltip="Close">
          <mat-icon>close</mat-icon>
        </button>
      </div>

      <section class="settings-dialog__section">
        <h3 class="settings-dialog__section-title">Feature mode</h3>
        <p class="settings-dialog__hint">Only one mode can be active. Also available as toggles in the chat bar.</p>
        <div class="settings-dialog__toggles">
          <button
            type="button"
            class="settings-dialog__toggle"
            [class.settings-dialog__toggle--active]="settings.isInternetSearchEnabled()"
            (click)="setMode('internet')">
            <mat-icon>language</mat-icon>
            <span>Internet Search</span>
          </button>
          <button
            type="button"
            class="settings-dialog__toggle"
            [class.settings-dialog__toggle--active]="settings.isDeepResearchEnabled()"
            (click)="setMode('deep')">
            <mat-icon>biotech</mat-icon>
            <span>Deep Research</span>
          </button>
          <button
            type="button"
            class="settings-dialog__toggle"
            [class.settings-dialog__toggle--active]="settings.isGsfEnabled()"
            (click)="setMode('gsf')">
            <mat-icon>storage</mat-icon>
            <span>NVIDIA Auto-Ontology</span>
          </button>
          <button
            type="button"
            class="settings-dialog__toggle"
            [class.settings-dialog__toggle--active]="settings.isAidrEnabled()"
            (click)="setMode('aidr')">
            <mat-icon>shield</mat-icon>
            <span>AIDR</span>
          </button>
        </div>
        @if (settings.featureMode() !== 'none') {
          <button type="button" class="settings-dialog__clear" (click)="clearMode()">Clear mode</button>
        }
      </section>

      <section class="settings-dialog__section">
        <h3 class="settings-dialog__section-title">Models in use</h3>
        <dl class="settings-dialog__info">
          <dt>Provider</dt>
          <dd>{{ settings.provider() || 'unknown' }}</dd>
          <dt>Chat / reasoning model</dt>
          <dd>{{ settings.modelId() || 'unknown' }}</dd>
          <dt>Embedding model (Insight Engine)</dt>
          <dd>{{ settings.embeddingModel() || 'unknown' }}</dd>
        </dl>
        <p class="settings-dialog__hint">
          Reported by the backend. Configured in the deployment, not changeable here.
        </p>
      </section>

      <div class="settings-dialog__footer">
        <button class="settings-dialog__done-btn" (click)="close()">Done</button>
      </div>
    </div>
  `,
  styles: [`
    :host { display: block; }

    .settings-dialog {
      padding: 1.25rem;
      box-sizing: border-box;
      color: #E8EBEC;
      min-width: min(420px, 90vw);

      &__header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 1rem;
      }

      &__title {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 1rem;
        font-weight: 600;

        mat-icon {
          font-size: 1.25rem;
          width: 1.25rem;
          height: 1.25rem;
          color: #00CEFF;
        }
      }

      &__close-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 2rem;
        height: 2rem;
        padding: 0;
        border: none;
        border-radius: 0.375rem;
        background: rgba(255, 255, 255, 0.1);
        color: #BCC5D4;
        cursor: pointer;

        &:hover {
          background: rgba(255, 255, 255, 0.2);
          color: #E8EBEC;
        }
      }

      &__section {
        margin-bottom: 1.25rem;
        padding-bottom: 1rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);

        &:last-of-type { border-bottom: none; }
      }

      &__section-title {
        margin: 0 0 0.35rem;
        font-size: 0.875rem;
        font-weight: 600;
        color: #E8EBEC;
      }

      &__hint {
        margin: 0 0 0.75rem;
        font-size: 0.7rem;
        color: #8B95A5;
        line-height: 1.4;
      }

      &__toggles {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
      }

      &__toggle {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.4rem 0.7rem;
        border: 1px solid rgba(255, 255, 255, 0.15);
        border-radius: 0.375rem;
        background: rgba(255, 255, 255, 0.06);
        color: #BCC5D4;
        cursor: pointer;
        font-size: 0.75rem;
        font-weight: 500;

        mat-icon {
          font-size: 1rem;
          width: 1rem;
          height: 1rem;
        }

        &:hover {
          background: rgba(255, 255, 255, 0.12);
          color: #E8EBEC;
        }

        &--active {
          background: rgba(0, 206, 255, 0.15);
          border-color: rgba(0, 206, 255, 0.45);
          color: #00CEFF;
        }
      }

      &__clear {
        margin-top: 0.6rem;
        padding: 0.25rem 0.5rem;
        border: none;
        background: transparent;
        color: #8B95A5;
        font-size: 0.7rem;
        cursor: pointer;
        text-decoration: underline;

        &:hover { color: #E8EBEC; }
      }

      &__info {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 0.4rem 1rem;
        margin: 0 0 0.75rem;
        font-size: 0.75rem;

        dt { color: #8B95A5; }

        dd {
          margin: 0;
          color: #E8EBEC;
          font-family: monospace;
          word-break: break-all;
        }
      }

      &__footer {
        display: flex;
        justify-content: flex-end;
        margin-top: 0.5rem;
      }

      &__done-btn {
        padding: 0.4rem 1rem;
        border: 1px solid rgba(0, 206, 255, 0.4);
        border-radius: 0.375rem;
        background: rgba(0, 206, 255, 0.15);
        color: #00CEFF;
        font-size: 0.8rem;
        font-weight: 600;
        cursor: pointer;

        &:hover { background: rgba(0, 206, 255, 0.25); }
      }
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppSettingsDialogComponent {
  settings = inject(FeatureSettingsService);
  private promptService = inject(SystemPromptService);
  private dialogRef = inject(MatDialogRef<AppSettingsDialogComponent>);

  setMode(mode: FeatureMode): void {
    this.settings.setFeatureMode(mode);
    const next = this.settings.featureMode();
    this.promptService.applyResearchMode(
      next === 'gsf' ? 'gsf' : next === 'deep' ? 'deep' : 'none'
    );
  }

  clearMode(): void {
    this.settings.featureMode.set('none');
    this.promptService.applyResearchMode('none');
  }

  close(): void {
    this.dialogRef.close();
  }
}
