import Ionicons from '@expo/vector-icons/Ionicons';
import type { ComponentProps } from 'react';

export type NomeIcone = ComponentProps<typeof Ionicons>['name'];

export function Icone({ nome, tamanho = 20, cor, style }: { nome: NomeIcone; tamanho?: number; cor: string; style?: ComponentProps<typeof Ionicons>['style'] }) {
  return <Ionicons name={nome} size={tamanho} color={cor} style={style} />;
}
