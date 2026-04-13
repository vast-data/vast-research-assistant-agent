import { ChangeDetectionStrategy, Component, computed, inject, signal, OnInit } from '@angular/core';
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

  sidebarOpen = computed(() => this.appStoreService.sidebarOpen());
  modelInfo = signal<{ provider: string; model_id: string } | null>(null);

  ngOnInit(): void {
    this.http.get<{ provider: string; model_id: string }>(`${SETTINGS.BASE_API_URL}/model-info`)
      .subscribe({
        next: (info) => this.modelInfo.set(info),
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

  toggleDeepResearch(): void {
    this.promptService.loadSystemPrompt();
    this.promptService.toggleDeepResearch();
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