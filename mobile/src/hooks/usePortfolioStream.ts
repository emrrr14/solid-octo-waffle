/**
 * Binds the websocket lifecycle to the app lifecycle.
 *
 * iOS suspends a backgrounded app within seconds and its sockets die without
 * notice; on return, a socket that *looks* connected delivers nothing.  So we
 * close deliberately on background and reconnect on foreground, which also
 * stops the app burning cellular data and battery in someone's pocket.  The
 * server answers every new connection with a full snapshot, so the
 * reconnect path needs no resume protocol - the frames do the work.
 */

import {useEffect, useRef} from 'react';
import {AppState, type AppStateStatus} from 'react-native';

import {PortfolioSocket} from '../api/socket';
import {SecureVault} from '../native/SecureVault';
import {usePortfolioStore} from '../state/portfolioStore';

export function usePortfolioStream(portfolioId: string, wsUrl: string): void {
  const socketRef = useRef<PortfolioSocket | null>(null);

  useEffect(() => {
    const {ingest, setStatus, setError, reset} = usePortfolioStore.getState();
    reset();

    const socket = new PortfolioSocket({
      url: wsUrl,
      portfolioId,
      getToken: () => SecureVault.readSession(),
      onFrame: ingest,
      onStatus: setStatus,
      onFatal: setError,
    });
    socketRef.current = socket;
    void socket.connect();

    const onAppStateChange = (next: AppStateStatus) => {
      if (next === 'active') {
        void socketRef.current?.connect();
      } else {
        // 'background' and 'inactive' both mean "stop streaming": inactive
        // covers the app switcher and incoming calls, where a live 1 Hz
        // stream is pure waste.
        socketRef.current?.close();
      }
    };

    const subscription = AppState.addEventListener('change', onAppStateChange);

    return () => {
      subscription.remove();
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [portfolioId, wsUrl]);
}
