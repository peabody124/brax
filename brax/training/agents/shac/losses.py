# Copyright 2022 The Brax Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Proximal policy optimization training.

See: https://arxiv.org/pdf/1707.06347.pdf
"""

from typing import Any, Tuple

from brax.training import types
from brax.training.agents.shac import networks as shac_networks
from brax.training.types import Params
import flax
import jax
import jax.numpy as jnp


@flax.struct.dataclass
class SHACNetworkParams:
  """Contains training state for the learner."""
  policy: Params
  value: Params


def compute_shac_policy_loss(
    policy_params: Params,
    value_params: Params,
    normalizer_params: Any,
    data: types.Transition,
    rng: jnp.ndarray,
    shac_network: shac_networks.SHACNetworks,
    entropy_cost: float = 1e-4,
    discounting: float = 0.9,
    reward_scaling: float = 1.0) -> Tuple[jnp.ndarray, types.Metrics]:
  """Computes SHAC critic loss.

  This implements Eq. 5 of 2204.07137. It needs to account for any episodes where
  the episode terminates and include the terminal values appopriately.

  Args:
    policy_params: Policy network parameters
    value_params: Value network parameters,
    normalizer_params: Parameters of the normalizer.
    data: Transition that with leading dimension [B, T]. extra fields required
      are ['state_extras']['truncation'] ['policy_extras']['raw_action']
        ['policy_extras']['log_prob']
    rng: Random key
    shac_network: SHAC networks.
    entropy_cost: entropy cost.
    discounting: discounting,
    reward_scaling: reward multiplier.

  Returns:
    A scalar loss
  """

  parametric_action_distribution = shac_network.parametric_action_distribution
  policy_apply = shac_network.policy_network.apply
  value_apply = shac_network.value_network.apply

  # Put the time dimension first.
  data = jax.tree_util.tree_map(lambda x: jnp.swapaxes(x, 0, 1), data)

  # this is a redundant computation with the critic loss function
  # but there isn't a straighforward way to get these values when
  # they are used in that step
  values = value_apply(normalizer_params, value_params, data.observation)
  terminal_values = value_apply(normalizer_params, value_params, data.next_observation[-1])

  rewards = data.reward * reward_scaling
  truncation = data.extras['state_extras']['truncation']
  termination = (1 - data.discount) * (1 - truncation)

  horizon = rewards.shape[0]

  def sum_step(carry, target_t):
    gam, acc = carry
    reward, v, truncation,  termination = target_t
    acc = acc + jnp.where(truncation + termination, gam * v, gam * reward)
    gam = jnp.where(termination, 1.0, gam * discounting)
    return (gam, acc), (acc)

  acc = terminal_values * (discounting ** horizon) * (1-termination[-1]) * (1-truncation[-1])
  jax.debug.print('acc shape: {x}', x=acc.shape)
  gam = jnp.ones_like(terminal_values)
  (_, acc), (temp) = jax.lax.scan(sum_step, (gam, acc),
      (rewards, values, truncation, termination))

  policy_loss = -jnp.mean(acc) / horizon

  # inspect the data for one of the rollouts
  jax.debug.print('obs={o}, obs_next={n}, values={v}, reward={r}, truncation={t}, terminal={s}',
      v=values[:, 0], o=data.observation[:,0], r=data.reward[:,0],
      t=truncation[:, 0], s=termination[:,0], n=data.next_observation[:, 0])

  jax.debug.print('loss={l}, r={r}', l=policy_loss, r=temp[:,0])

  # Entropy reward
  policy_logits = policy_apply(normalizer_params, policy_params,
                               data.observation)
  entropy = jnp.mean(parametric_action_distribution.entropy(policy_logits, rng))
  entropy_loss = entropy_cost * -entropy

  total_loss = policy_loss + entropy_loss

  return total_loss, {
    'total_loss': total_loss,
    'policy_loss': policy_loss,
    'entropy_loss': entropy_loss
  }



def compute_target_values(truncation: jnp.ndarray,
                          termination: jnp.ndarray,
                          rewards: jnp.ndarray,
                          values: jnp.ndarray,
                          bootstrap_value: jnp.ndarray,
                          discount: float = 0.99,
                          lambda_: float = 0.95,
                          td_lambda=True):
  """Calculates the target values.

  This implements Eq. 7 of 2204.07137
  https://github.com/NVlabs/DiffRL/blob/main/algorithms/shac.py#L349

  Args:
    truncation: A float32 tensor of shape [T, B] with truncation signal.
    termination: A float32 tensor of shape [T, B] with termination signal.
    rewards: A float32 tensor of shape [T, B] containing rewards generated by
      following the behaviour policy.
    values: A float32 tensor of shape [T, B] with the value function estimates
      wrt. the target policy.
    bootstrap_value: A float32 of shape [B] with the value function estimate at
      time T.
    discount: TD discount.

  Returns:
    A float32 tensor of shape [T, B].
  """
  truncation_mask = 1 - truncation
  # Append bootstrapped value to get [v1, ..., v_t+1]
  values_t_plus_1 = jnp.concatenate(
      [values[1:], jnp.expand_dims(bootstrap_value, 0)], axis=0)

  if td_lambda:

    def compute_v_st(carry, target_t):
      Ai, Bi, lam = carry
      reward, truncation_mask, vtp1, termination = target_t
      # TODO: should figure out how to handle termination

      lam = lam * lambda_ * (1 - termination) + termination
      Ai = (1 - termination) * (lam * discount * Ai + discount * vtp1 + (1. - lam) / (1. - lambda_) * reward)
      Bi = discount * (vtp1 * termination + Bi * (1.0 - termination)) + reward
      vs = (1.0 - lambda_) * Ai + lam * Bi

      return (Ai, Bi, lam), (vs)

    Ai = jnp.ones_like(bootstrap_value)
    Bi = jnp.zeros_like(bootstrap_value)
    lam = jnp.ones_like(bootstrap_value)

    (_, _, _), (vs) = jax.lax.scan(compute_v_st, (Ai, Bi, lam),
        (rewards, truncation_mask, values_t_plus_1, termination),
        length=int(truncation_mask.shape[0]),
        reverse=True)

  else:
    vs = rewards + discount * values_t_plus_1

  return jax.lax.stop_gradient(vs)


def compute_shac_critic_loss(
    params: Params,
    normalizer_params: Any,
    data: types.Transition,
    rng: jnp.ndarray,
    shac_network: shac_networks.SHACNetworks,
    discounting: float = 0.9,
    reward_scaling: float = 1.0,
    lambda_: float = 0.95) -> Tuple[jnp.ndarray, types.Metrics]:
  """Computes SHAC critic loss.

  Args:
    params: Value network parameters,
    normalizer_params: Parameters of the normalizer.
    data: Transition that with leading dimension [B, T]. extra fields required
      are ['state_extras']['truncation'] ['policy_extras']['raw_action']
        ['policy_extras']['log_prob']
    rng: Random key
    shac_network: SHAC networks.
    entropy_cost: entropy cost.
    discounting: discounting,
    reward_scaling: reward multiplier.
    lambda_: Lambda for TD value updates
    clipping_epsilon: Policy loss clipping epsilon
    normalize_advantage: whether to normalize advantage estimate

  Returns:
    A tuple (loss, metrics)
  """

  value_apply = shac_network.value_network.apply

  data = jax.tree_util.tree_map(lambda x: jnp.swapaxes(x, 0, 1), data)

  baseline = value_apply(normalizer_params, params, data.observation)
  bootstrap_value = value_apply(normalizer_params, params, data.next_observation[-1])

  rewards = data.reward * reward_scaling
  truncation = data.extras['state_extras']['truncation']
  termination = (1 - data.discount) * (1 - truncation)

  vs = compute_target_values(
      truncation=truncation,
      termination=termination,
      rewards=rewards,
      values=baseline,
      bootstrap_value=bootstrap_value,
      discount=discounting,
      lambda_=lambda_)

  v_error = vs - baseline
  v_loss = jnp.mean(v_error * v_error) * 0.5 * 0.5


  total_loss = v_loss
  return total_loss, {
      'total_loss': total_loss,
      'policy_loss': 0,
      'v_loss': v_loss,
      'entropy_loss': 0
  }
