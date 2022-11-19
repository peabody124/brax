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


def compute_policy_loss(truncation: jnp.ndarray,
                        termination: jnp.ndarray,
                        rewards: jnp.ndarray,
                        values: jnp.ndarray,
                        bootstrap_value: jnp.ndarray,
                        discount: float = 0.99):
  """Calculates the short horizon reward.

  This implements Eq. 5 of 2204.07137. It needs to account for any episodes where
  the episode terminates and include the terminal values appopriately.

  Adopted from ppo.losses.compute_gae

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
    A scalar loss.
  """

  horizon = rewards.shape[0]
  truncation_mask = 1 - truncation
  # Append bootstrapped value to get [v1, ..., v_t+1]
  values_t_plus_1 = jnp.concatenate([values[1:], jnp.expand_dims(bootstrap_value, 0)], axis=0)

  def sum_step(carry, target_t):
    gam, acc = carry
    reward, truncation_mask, vtp1, termination = target_t
    gam = jnp.where(termination, 1.0, gam * discount)
    acc = acc + truncation_mask * jnp.where(termination, 0, gam * reward)
    return (gam, acc), (None)

  acc = bootstrap_value * (discount ** horizon)
  gam = jnp.ones_like(bootstrap_value)
  (_, acc), (_) = jax.lax.scan(sum_step, (gam, acc),
      (rewards, truncation_mask, values_t_plus_1, termination))

  loss = -jnp.mean(acc) / horizon
  return loss


def compute_target_values(truncation: jnp.ndarray,
                          termination: jnp.ndarray,
                          rewards: jnp.ndarray,
                          values: jnp.ndarray,
                          bootstrap_value: jnp.ndarray,
                          discount: float = 0.99,
                          lambda_: float = 0.95,
                          td_lambda=False):
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


def compute_shac_loss(
    params: Params,
    normalizer_params: Any,
    data: types.Transition,
    rng: jnp.ndarray,
    shac_network: shac_networks.SHACNetworks,
    entropy_cost: float = 1e-4,
    discounting: float = 0.9,
    reward_scaling: float = 1.0,
    lambda_: float = 0.95,
    clipping_epsilon: float = 0.3) -> Tuple[jnp.ndarray, types.Metrics]:
  """Computes SHAC loss.

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
  parametric_action_distribution = shac_network.parametric_action_distribution
  #policy_apply = shac_network.policy_network.apply
  value_apply = shac_network.value_network.apply

  # Put the time dimension first.
  data = jax.tree_util.tree_map(lambda x: jnp.swapaxes(x, 0, 1), data)
  #policy_logits = policy_apply(normalizer_params, params.policy,
  #                             data.observation)

  baseline = value_apply(normalizer_params, params, data.observation)
  bootstrap_value = value_apply(normalizer_params, params, data.next_observation[-1])

  rewards = data.reward * reward_scaling
  truncation = data.extras['state_extras']['truncation']
  termination = (1 - data.discount) * (1 - truncation)

  # compute policy loss
  policy_loss = compute_policy_loss(
      truncation=truncation,
      termination=termination,
      rewards=rewards,
      values=baseline,
      bootstrap_value=bootstrap_value,
      discount=discounting)

  policy_loss = -jnp.mean(rewards)

  vs = compute_target_values(
      truncation=truncation,
      termination=termination,
      rewards=rewards,
      values=baseline,
      bootstrap_value=bootstrap_value,
      discount=discounting,
      lambda_=lambda_)

  if False:
    from ..ppo.losses import compute_gae
    vs, advantages = compute_gae(
        truncation=truncation,
        termination=termination,
        rewards=rewards,
        values=baseline,
        bootstrap_value=bootstrap_value,
        lambda_=0.95,
        discount=discounting)

  v_error = vs - baseline
  v_loss = jnp.mean(v_error * v_error) * 0.5 * 0.5

  jax.debug.print("LOSS {loss} MEAN TARGET {targets} V_LOSS {v_loss} MEAN_REWARD {x} MEAN BOOTSTRAP {y}",
                   loss=policy_loss, targets=jnp.mean(vs), v_loss=v_loss, x=jnp.mean(rewards),
                   y=jnp.mean(bootstrap_value))

  # Entropy reward
  #entropy = jnp.mean(parametric_action_distribution.entropy(policy_logits, rng))
  #entropy_loss = entropy_cost * -entropy
  entropy_loss = 0

  total_loss = policy_loss #+ v_loss + entropy_loss
  return total_loss, {
      'total_loss': total_loss,
      'policy_loss': policy_loss,
      'v_loss': v_loss,
      'entropy_loss': entropy_loss
  }
