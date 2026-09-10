const { spawn } = require('child_process');

function startTunnel() {
  console.log('[Tunnel] Launching persistent SSH HTTPS tunnel...');
  const child = spawn('ssh', [
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'ServerAliveInterval=15',
    '-o', 'ServerAliveCountMax=6',
    '-R', '80:127.0.0.1:5000',
    'nokey@localhost.run'
  ]);

  child.stdout.on('data', (d) => {
    const str = d.toString();
    console.log('[Tunnel]', str.trim());
  });

  child.stderr.on('data', (d) => {
    const str = d.toString();
    if (!str.includes('Pseudo-terminal')) {
      console.log('[Tunnel Info]', str.trim());
    }
  });

  child.on('close', (code) => {
    console.warn(`[Tunnel] Closed (code ${code}). Auto-reconnecting in 2 seconds...`);
    setTimeout(startTunnel, 2000);
  });
}

startTunnel();
