import { ChangeDetectionStrategy, Component, computed, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { MatIcon } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatMenuModule } from '@angular/material/menu';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { UserLogoComponent } from '../../../features/shared/components/user-logo/user-logo.component';
import { AppStoreService } from '../../../features/shared/services/app-store.service';
import { SystemPromptService } from '../../../features/shared/services/system-prompt.service';
import { SystemPromptDialogComponent } from '../../../features/shared/components/system-prompt-dialog/system-prompt-dialog.component';
import { AppSettingsDialogComponent } from '../../../features/shared/components/app-settings-dialog/app-settings-dialog.component';
import { FEATURE_MODE_LABELS, FeatureSettingsService, ModelInfo } from '../../../features/shared/services/feature-settings.service';
import { SETTINGS } from '../../../features/shared/settings';

@Component({
  selector: 'app-top-bar',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    MatIcon,
    MatTooltipModule,
    MatMenuModule,
    MatDialogModule,
    UserLogoComponent,
  ],
  templateUrl: './top-bar.component.html',
  styleUrl: './top-bar.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TopBarComponent implements OnInit {
  private appStoreService = inject(AppStoreService);
  private dialog = inject(MatDialog);
  private http = inject(HttpClient);
  promptService = inject(SystemPromptService);
  featureSettings = inject(FeatureSettingsService);
  readonly featureModeLabels = FEATURE_MODE_LABELS;

  sidebarOpen = computed(() => this.appStoreService.sidebarOpen());
  modelInfo = computed(() =>
    this.featureSettings.modelId()
      ? { provider: this.featureSettings.provider(), model_id: this.featureSettings.modelId() }
      : null
  );

  ngOnInit(): void {
    this.http.get<ModelInfo>(`${SETTINGS.BASE_API_URL}/model-info`).subscribe({
      next: (info) => this.featureSettings.setModelInfo(info),
      error: () => {}
    });
  }

  toggleSidebar(): void {
    this.appStoreService.toggleSidebar();
  }

  onLogoClick(): void {
    this.appStoreService.closeSidebar();
  }

  onNewChat(): void {
    this.appStoreService.closeSidebar();
  }

  openSettings(): void {
    this.promptService.loadSystemPrompt();
    this.dialog.open(AppSettingsDialogComponent, {
      width: '480px',
      maxWidth: '90vw',
      panelClass: 'system-prompt-dialog-panel',
      backdropClass: 'system-prompt-dialog-backdrop',
      autoFocus: false,
    });
  }

  openSystemPrompt(): void {
    this.promptService.loadSystemPrompt();
    this.dialog.open(SystemPromptDialogComponent, {
      width: '600px',
      maxWidth: '90vw',
      panelClass: 'system-prompt-dialog-panel',
      backdropClass: 'system-prompt-dialog-backdrop',
      autoFocus: 'textarea',
    });
  }
}
