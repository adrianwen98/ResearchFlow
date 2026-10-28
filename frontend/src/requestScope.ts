/** A task selection/stream session owns its requests, even for A → B → A. */
export class RequestScope {
  private revision = 0;
  invalidate() { this.revision++; }
  capture() {
    const revision = this.revision;
    return () => this.revision === revision;
  }
}
